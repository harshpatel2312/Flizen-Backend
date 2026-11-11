"""
Duffel Air API integration service for flight search and offer requests
"""
import httpx
from typing import List, Optional
from datetime import datetime
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.flight_schema import (
    DuffelFlightOffer,
    SegmentInfo,
    SliceSummary,
    FlightCondition,
    FlightConditions,
    BaggageAllowance,
)

logger = get_logger(__name__)


class DuffelOfferNotFoundError(Exception):
    """Raised when Duffel returns 404/410 for an offer lookup (expired or unknown)."""


class DuffelServiceError(Exception):
    """Raised for any other non-success Duffel API response."""


class DuffelService:
    """Service for Duffel Air API interactions"""
    
    def __init__(self):
        self.base_url = settings.DUFFEL_API_BASE_URL
        self.access_token = settings.DUFFEL_ACCESS_TOKEN
        self.api_version = settings.DUFFEL_API_VERSION
        self.headers = self._get_headers()
    
    def _get_headers(self) -> dict:
        """Build request headers for Duffel API"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Duffel-Version": self.api_version,
            "Content-Type": "application/json",
        }
    
    async def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = "economy",
        currency: Optional[str] = None,
        return_date: Optional[str] = None,
    ) -> List[DuffelFlightOffer]:
        """
        Search for flight offers using Duffel API.

        Round-trip: pass ``return_date`` and the request will include a second
        slice (destination -> origin). The returned offers each contain both
        slices, share a single ``id`` and a single ``total_amount`` — Duffel's
        authoritative combined round-trip price.
        """
        if not self.access_token:
            logger.error("Duffel access token not configured")
            raise ValueError("Duffel access token is not configured. Set DUFFEL_ACCESS_TOKEN in .env")

        payload = self._build_offer_request(
            origin,
            destination,
            departure_date,
            adults,
            children,
            infants,
            cabin_class,
            currency,
            return_date=return_date,
        )
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/air/offer_requests",
                    json=payload,
                    headers=self.headers,
                )
                
                if response.status_code != 201:
                    # Log status code only — response body may contain sensitive API data
                    logger.error("Duffel API returned HTTP %d", response.status_code)
                    raise Exception(
                        f"Duffel API returned status {response.status_code}"
                    )
                
                data = response.json()
                return self._parse_offer_response(data)
        
        except httpx.TimeoutException:
            logger.error("Duffel API request timed out")
            raise Exception("Flight search timed out. Please try again.")
        except httpx.RequestError as e:
            logger.error("Duffel API request failed: %s", e)
            raise Exception("Unable to connect to flight search service.")
        except Exception as e:
            logger.error("Error searching flights: %s", e)
            raise

    async def get_offer(self, offer_id: str) -> dict:
        """Fetch a single offer from Duffel to revalidate price/availability.

        Returns the raw `data` object from Duffel. Raises
        DuffelOfferNotFoundError when the offer is gone (expired or
        unknown) and DuffelServiceError for any other failure.
        """
        if not self.access_token:
            logger.error("Duffel access token not configured")
            raise DuffelServiceError("Duffel access token is not configured")
        if not offer_id or not isinstance(offer_id, str):
            raise DuffelOfferNotFoundError("offer_id is required")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/air/offers/{offer_id}",
                    headers=self.headers,
                )
        except httpx.TimeoutException as exc:
            logger.error("Duffel get_offer timed out for offer")
            raise DuffelServiceError("Duffel offer lookup timed out") from exc
        except httpx.RequestError as exc:
            logger.error("Duffel get_offer network error: %s", type(exc).__name__)
            raise DuffelServiceError("Duffel offer lookup failed") from exc

        if response.status_code in (404, 410):
            # Offer_id intentionally omitted from logs to avoid leaking IDs.
            logger.info("Duffel offer lookup returned %d", response.status_code)
            raise DuffelOfferNotFoundError("Offer not found or expired")
        if response.status_code != 200:
            logger.error("Duffel get_offer HTTP %d", response.status_code)
            raise DuffelServiceError(f"Duffel returned status {response.status_code}")

        payload = response.json()
        return payload.get("data") or {}

    async def create_order(
        self,
        *,
        offer_id: str,
        amount: str,
        currency: str,
        passengers: list,
        idempotency_key: str,
    ) -> dict:
        """Create a Duffel order (instant, paid via Balance).

        Follows the payload shape documented in the Duffel "Create an order"
        guide: `data.selected_offers`, `data.payments`, `data.passengers`.
        Never sends services / seat_services — local seat preferences are UI
        hints only and are NOT confirmed with Duffel here.
        """
        if not self.access_token:
            raise DuffelServiceError("Duffel access token is not configured")
        if not offer_id:
            raise DuffelServiceError("offer_id is required")

        payload = {
            "data": {
                "type": "instant",
                "selected_offers": [offer_id],
                "payments": [{
                    "type": "balance",
                    "currency": str(currency).upper(),
                    "amount": str(amount),
                }],
                "passengers": passengers,
            }
        }
        headers = {**self.headers, "Idempotency-Key": idempotency_key}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/air/orders",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            logger.error("Duffel create_order timed out")
            raise DuffelServiceError("Duffel order creation timed out") from exc
        except httpx.RequestError as exc:
            logger.error("Duffel create_order network error: %s", type(exc).__name__)
            raise DuffelServiceError("Duffel order creation failed") from exc

        if response.status_code == 201:
            return response.json().get("data") or {}

        # Log only Duffel's structured error codes (title/type/code/message) —
        # these are documented and PII-free. Never log the full body.
        logger.error("Duffel create_order HTTP %d", response.status_code)
        try:
            body = response.json() or {}
            for err in (body.get("errors") or [])[:5]:
                logger.error(
                    "Duffel error title=%r type=%r code=%r message=%r",
                    err.get("title"),
                    err.get("type"),
                    err.get("code"),
                    err.get("message"),
                )
        except Exception:
            pass
        raise DuffelServiceError(
            f"Duffel order creation returned status {response.status_code}"
        )

    def _build_offer_request(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        adults: int,
        children: int,
        infants: int,
        cabin_class: str,
        currency: Optional[str] = None,
        return_date: Optional[str] = None,
    ) -> dict:
        """Build Duffel offer request payload.

        One slice for one-way; add a second (destination -> origin) slice when
        ``return_date`` is provided. Duffel returns per-slice segments plus a
        single ``total_amount`` for the whole itinerary.
        """
        passengers = []
        for _ in range(adults):
            passengers.append({"type": "adult"})
        for _ in range(children):
            passengers.append({"type": "child"})
        for _ in range(infants):
            passengers.append({"type": "infant"})

        slices = [{
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
        }]
        if return_date:
            slices.append({
                "origin": destination.upper(),
                "destination": origin.upper(),
                "departure_date": return_date,
            })

        data: dict = {
            "slices": slices,
            "passengers": passengers,
            "cabin_class": cabin_class.lower(),
        }

        if currency:
            data["currency"] = currency.upper()

        return {"data": data}
    
    def _parse_offer_response(self, response_data: dict) -> List[DuffelFlightOffer]:
        """Parse Duffel API response and convert to frontend-friendly format"""
        
        flights = []
        
        # Extract offers from response
        offers = response_data.get("data", {}).get("offers", [])
        
        if not offers:
            logger.warning("No offers returned from Duffel API")
            return flights
        
        # Parse each offer
        for offer in offers:
            try:
                flight = self._parse_single_offer(offer)
                if flight:
                    flights.append(flight)
            except Exception as e:
                logger.warning("Failed to parse offer: %s", e, exc_info=True)
                continue
        
        return flights
    
    def _parse_single_offer(self, offer: dict) -> Optional[DuffelFlightOffer]:
        """Parse a single Duffel offer into DuffelFlightOffer.

        A round-trip offer has two slices; both share this offer's id and its
        single ``total_amount``. Per-slice display info is emitted in ``slices``.
        """
        try:
            slices = offer.get("slices", [])
            if not slices:
                return None

            # Build per-slice summaries (includes segments) so the frontend can
            # render outbound and return legs from ONE offer.
            slice_summaries: List[SliceSummary] = []
            for sl in slices:
                sl_segs = sl.get("segments") or []
                if not sl_segs:
                    continue
                sl_first, sl_last = sl_segs[0], sl_segs[-1]
                sl_origin = ((sl_first.get("origin") or {}).get("iata_code") or "").upper()
                sl_dest   = ((sl_last.get("destination") or {}).get("iata_code") or "").upper()
                sl_dep    = sl_first.get("departing_at") or ""
                sl_arr    = sl_last.get("arriving_at") or ""
                sl_seg_infos = []
                for seg in sl_segs:
                    sl_seg_infos.append(SegmentInfo(
                        airline=(seg.get("operating_carrier") or {}).get("name") or "Unknown",
                        flight_number=seg.get("operating_carrier_flight_number") or "",
                        origin=((seg.get("origin") or {}).get("iata_code") or "").upper(),
                        destination=((seg.get("destination") or {}).get("iata_code") or "").upper(),
                        departure_datetime=seg.get("departing_at") or "",
                        arrival_datetime=seg.get("arriving_at") or "",
                        duration=seg.get("duration") or None,
                        aircraft_name=(seg.get("aircraft") or {}).get("name") or None,
                    ))
                slice_summaries.append(SliceSummary(
                    origin=sl_origin,
                    destination=sl_dest,
                    departure_time=sl_dep[11:16] if len(sl_dep) > 10 else "00:00",
                    arrival_time=sl_arr[11:16] if len(sl_arr) > 10 else "00:00",
                    departure_datetime=sl_dep,
                    arrival_datetime=sl_arr,
                    duration=self._calculate_duration(sl_dep, sl_arr),
                    stops=max(0, len(sl_segs) - 1),
                    segments=sl_seg_infos,
                ))

            if not slice_summaries:
                return None

            first_slice = slices[0]
            segments = first_slice.get("segments", [])
            if not segments:
                return None

            first_segment = segments[0]
            last_segment = segments[-1]

            # --- Airline ---
            airline_data = first_segment.get("operating_carrier") or {}
            airline_name = airline_data.get("name") or "Unknown Airline"
            airline_logo = airline_data.get("logo_symbol_url") or None

            # --- Flight number ---
            flight_number = first_segment.get("operating_carrier_flight_number") or ""

            # --- Origin / destination ---
            origin = (first_segment.get("origin") or {}).get("iata_code", "").upper()
            destination = (last_segment.get("destination") or {}).get("iata_code", "").upper()

            # --- Times ---
            departure_str = first_segment.get("departing_at") or ""
            arrival_str = last_segment.get("arriving_at") or ""
            departure_time = departure_str[11:16] if len(departure_str) > 10 else "00:00"
            arrival_time = arrival_str[11:16] if len(arrival_str) > 10 else "00:00"

            # --- Duration (whole itinerary) ---
            duration = self._calculate_duration(departure_str, arrival_str)

            # --- Stops = number of connections (segment count - 1) ---
            stops = max(0, len(segments) - 1)

            # --- Price ---
            total_price = offer.get("total_amount") or "0"
            currency = offer.get("total_currency") or "USD"

            # --- Segments ---
            segment_list = []
            for seg in segments:
                seg_airline = (seg.get("operating_carrier") or {}).get("name") or "Unknown"
                seg_fn = seg.get("operating_carrier_flight_number") or ""
                seg_origin = ((seg.get("origin") or {}).get("iata_code") or "").upper()
                seg_dest = ((seg.get("destination") or {}).get("iata_code") or "").upper()
                seg_dep = seg.get("departing_at") or ""
                seg_arr = seg.get("arriving_at") or ""
                seg_dur = seg.get("duration") or None
                aircraft_obj = seg.get("aircraft") or {}
                seg_aircraft = aircraft_obj.get("name") or None
                segment_list.append(SegmentInfo(
                    airline=seg_airline,
                    flight_number=seg_fn,
                    origin=seg_origin,
                    destination=seg_dest,
                    departure_datetime=seg_dep,
                    arrival_datetime=seg_arr,
                    duration=seg_dur,
                    aircraft_name=seg_aircraft,
                ))

            # --- Cabin class (from first passenger on first segment) ---
            cabin_class = None
            pax_list = (segments[0].get("passengers") or [])
            if pax_list:
                cabin_class = (
                    pax_list[0].get("cabin_class_marketing_name")
                    or pax_list[0].get("cabin_class")
                    or None
                )

            # --- Fare brand ---
            fare_brand_name = first_slice.get("fare_brand_name") or None

            # --- Conditions ---
            raw_cond = offer.get("conditions") or {}
            conditions = None
            if raw_cond:
                def _parse_cond(c: dict) -> Optional[FlightCondition]:
                    if not c:
                        return None
                    return FlightCondition(
                        allowed=c.get("allowed"),
                        penalty_amount=c.get("penalty_amount"),
                        penalty_currency=c.get("penalty_currency"),
                    )
                conditions = FlightConditions(
                    refund_before_departure=_parse_cond(raw_cond.get("refund_before_departure")),
                    change_before_departure=_parse_cond(raw_cond.get("change_before_departure")),
                )

            # --- Baggages (from first passenger on first segment) ---
            baggages = []
            if pax_list:
                for b in (pax_list[0].get("baggages") or []):
                    baggages.append(BaggageAllowance(
                        type=b.get("type") or "checked",
                        quantity=b.get("quantity") or 0,
                        max_weight_kg=b.get("max_weight_kg"),
                    ))

            return DuffelFlightOffer(
                id=offer.get("id") or "",
                airline=airline_name,
                airline_logo=airline_logo,
                flight_number=flight_number,
                origin=origin,
                destination=destination,
                departure_time=departure_time,
                arrival_time=arrival_time,
                departure_datetime=departure_str,
                arrival_datetime=arrival_str,
                duration=duration,
                stops=stops,
                price=total_price,
                currency=currency,
                segments=segment_list,
                slices=slice_summaries,
                slice_count=len(slice_summaries),
                cabin_class=cabin_class,
                fare_brand_name=fare_brand_name,
                conditions=conditions,
                baggages=baggages,
                total_emissions_kg=offer.get("total_emissions_kg") or None,
                passport_required=offer.get("passenger_identity_documents_required"),
            )

        except Exception as e:
            logger.warning("Error parsing single offer: %s", e, exc_info=True)
            return None
    
    def _calculate_duration(self, departure: str, arrival: str) -> str:
        """Calculate flight duration in ISO 8601 format (PT1H35M)"""
        try:
            if not departure or not arrival:
                return "PT0H"
            
            dep_dt = datetime.fromisoformat(departure.replace("Z", "+00:00"))
            arr_dt = datetime.fromisoformat(arrival.replace("Z", "+00:00"))
            
            delta = arr_dt - dep_dt
            total_seconds = int(delta.total_seconds())
            
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            
            if hours == 0 and minutes == 0:
                return "PT0H"
            elif hours == 0:
                return f"PT{minutes}M"
            elif minutes == 0:
                return f"PT{hours}H"
            else:
                return f"PT{hours}H{minutes}M"
        
        except Exception as e:
            logger.warning(f"Failed to calculate duration: {str(e)}")
            return "PT0H"
