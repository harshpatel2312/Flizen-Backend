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
    FlightCondition,
    FlightConditions,
    BaggageAllowance,
)

logger = get_logger(__name__)


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
    ) -> List[DuffelFlightOffer]:
        """
        Search for flight offers using Duffel API
        
        Args:
            origin: IATA airport code (e.g., YYZ)
            destination: IATA airport code (e.g., JFK)
            departure_date: Date in YYYY-MM-DD format
            adults: Number of adult passengers
            children: Number of child passengers
            infants: Number of infant passengers
            cabin_class: Cabin class (economy, premium_economy, business, first)
        
        Returns:
            List of DuffelFlightOffer objects
        """
        if not self.access_token:
            logger.error("Duffel access token not configured")
            raise ValueError("Duffel access token is not configured. Set DUFFEL_ACCESS_TOKEN in .env")
        
        # Build offer request payload
        payload = self._build_offer_request(
            origin,
            destination,
            departure_date,
            adults,
            children,
            infants,
            cabin_class,
            currency,
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
    ) -> dict:
        """Build Duffel offer request payload"""
        
        # Build passengers list
        passengers = []
        for _ in range(adults):
            passengers.append({"type": "adult"})
        for _ in range(children):
            passengers.append({"type": "child"})
        for _ in range(infants):
            passengers.append({"type": "infant"})
        
        data: dict = {
            "slices": [
                {
                    "origin": origin.upper(),
                    "destination": destination.upper(),
                    "departure_date": departure_date,
                }
            ],
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
        """Parse a single Duffel offer into DuffelFlightOffer"""
        try:
            slices = offer.get("slices", [])
            if not slices:
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
