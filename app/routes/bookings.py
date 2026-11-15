"""Booking routes.

- Create a draft:      POST /api/bookings/draft
- Booking history:     GET  /api/bookings
- Booking summary:     GET  /api/bookings/{booking_id}          (status polling)
- Trip details:        GET  /api/bookings/{booking_id}/details  (My Trips)

Amount and currency are taken from Duffel, never from the client. Local seat
picks are stored as UI preferences only and are never treated as
Duffel-confirmed seat services. Bookings live in Firestore `bookings/{id}`;
payment attempts live in `bookings/{id}/payments/{payment_intent_id}` and are
written only by the payments route and verified Stripe webhooks.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from google.api_core.exceptions import FailedPrecondition

from app.core.auth import verified_uid
from app.core.firebase import get_db
from app.core.logging import get_logger
from app.models.booking import (
    BookingDetails,
    BookingDraftRequest,
    BookingDraftResponse,
    BookingSummary,
    BookingsListResponse,
    FlightSummary,
    Itinerary,
    OrderDetails,
    PaymentRecordSummary,
    TravellerSummary,
)
from app.services.booking_records import list_payment_records
from app.services.duffel_service import (
    DuffelOfferNotFoundError,
    DuffelService,
    DuffelServiceError,
)
from app.services.itinerary import airline_logo_url, extract_itinerary, extract_order_details
from app.services.stripe_service import from_stripe_minor_units

logger = get_logger(__name__)
router = APIRouter(prefix="/api/bookings", tags=["bookings"])

_DRAFT_STATUS = "draft_awaiting_payment"
# Upper bound for one history page; far above any realistic per-user count.
MAX_LISTED_BOOKINGS = 200

# Module-level singleton so tests can override the dependency without touching state.
_duffel_service = DuffelService()


def get_duffel_service() -> DuffelService:
    return _duffel_service


def get_bookings_db():
    return get_db()


def _passenger_types(offer: dict) -> list[str]:
    return [(p.get("type") or "").lower() for p in (offer.get("passengers") or [])]


def _extract_flight_summary(offer: dict) -> dict:
    """Pull display-only fields from a raw Duffel offer payload.

    Handles both one-way (1 slice) and round-trip (2 slices) offers. The
    return slice, when present, is emitted with `return_*` field names.
    Safe on missing/misshapen data — returns whatever we can and ignores the rest.
    """
    try:
        slices = offer.get("slices") or []
        if not slices:
            return {}

        def _slice_display(sl):
            segs = sl.get("segments") or []
            if not segs:
                return None
            first, last = segs[0], segs[-1]
            origin  = ((first.get("origin")      or {}).get("iata_code") or "").upper() or None
            dest    = ((last.get("destination")  or {}).get("iata_code") or "").upper() or None
            airline = ((first.get("operating_carrier") or {}).get("name") or "") or None
            dep     = first.get("departing_at") or ""
            dep_date = dep[:10] if dep else None
            return {"origin": origin, "destination": dest, "airline": airline, "date": dep_date}

        out = _slice_display(slices[0]) or {}
        summary = {
            "from_airport":   out.get("origin"),
            "to_airport":     out.get("destination"),
            "airline":        out.get("airline"),
            "departure_date": out.get("date"),
            "is_round_trip":  len(slices) > 1,
            "airline_logo_url": airline_logo_url(offer),
        }
        if len(slices) > 1:
            ret = _slice_display(slices[1]) or {}
            summary["return_from_airport"]   = ret.get("origin")
            summary["return_to_airport"]     = ret.get("destination")
            summary["return_departure_date"] = ret.get("date")
        return summary
    except Exception:
        return {}


def _parse_itinerary(data: dict) -> Optional[Itinerary]:
    raw = data.get("itinerary")
    if not isinstance(raw, dict) or not raw.get("slices"):
        return None
    try:
        return Itinerary(**raw)
    except Exception:
        return None


def _parse_order_details(data: dict) -> Optional[OrderDetails]:
    raw = data.get("order_details")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return OrderDetails(**raw)
    except Exception:
        return None


def _to_summary(data: dict) -> BookingSummary:
    """Project a raw Firestore booking doc to the safe wire model.

    Excludes any Stripe/Duffel identifiers or PII beyond what the client needs.
    """
    status = data.get("status")
    fs_raw = data.get("flight_summary") or None
    return BookingSummary(
        booking_id      = str(data.get("booking_id") or ""),
        status          = str(status or _DRAFT_STATUS),
        amount          = str(data.get("amount") or ""),
        currency        = str(data.get("currency") or ""),
        slice_count     = int(data.get("slice_count") or 0),
        passenger_count = int(data.get("passenger_count") or 0),
        payment_status  = data.get("payment_status"),
        refund_state    = data.get("refund_state"),
        failure_reason  = data.get("failure_reason"),
        # Never reveal a Duffel booking reference until Duffel has actually confirmed.
        duffel_booking_reference = (
            data.get("duffel_booking_reference") if status == "confirmed" else None
        ),
        flight_summary  = FlightSummary(**fs_raw) if isinstance(fs_raw, dict) else None,
        itinerary       = _parse_itinerary(data),
        created_at      = data.get("created_at"),
        updated_at      = data.get("updated_at"),
        confirmed_at    = data.get("confirmed_at"),
        offer_expires_at= data.get("offer_expires_at"),
    )


def _to_payment_summary(record: dict, booking_currency: Optional[str] = None) -> PaymentRecordSummary:
    currency = record.get("currency") or booking_currency
    return PaymentRecordSummary(
        status          = record.get("status"),
        amount          = record.get("amount") or from_stripe_minor_units(record.get("amount_minor"), currency),
        currency        = currency,
        amount_refunded = from_stripe_minor_units(record.get("amount_refunded_minor"), currency),
        refund_status   = record.get("refund_status"),
        failure_code    = record.get("failure_code"),
        created_at      = record.get("created_at"),
        succeeded_at    = record.get("succeeded_at"),
        failed_at       = record.get("failed_at"),
        canceled_at     = record.get("canceled_at"),
        refunded_at     = record.get("refunded_at"),
    )


def _to_details(data: dict, payments: list[dict]) -> BookingDetails:
    """Owner-only trip view. Traveller names only — no DOB, passport, or phone."""
    travellers = [
        TravellerSummary(
            given_name  = str(p.get("given_name") or ""),
            family_name = str(p.get("family_name") or ""),
            type        = str(p.get("type") or "adult"),
        )
        for p in (data.get("passengers") or [])
        if isinstance(p, dict)
    ]

    return BookingDetails(
        **_to_summary(data).model_dump(),
        travellers          = travellers,
        order_details       = _parse_order_details(data),
        duffel_order_id     = data.get("duffel_order_id"),
        contact_email       = (data.get("contact") or {}).get("email"),
        payments            = [_to_payment_summary(p, data.get("currency")) for p in payments],
        payment_received_at = data.get("payment_received_at"),
        payment_failed_at   = data.get("payment_failed_at"),
        canceled_at         = data.get("canceled_at"),
        refund_initiated_at = data.get("refund_initiated_at"),
        refunded_at         = data.get("refunded_at"),
    )


def _draft_fingerprint(uid: str, body: BookingDraftRequest) -> str:
    """Stable hash of everything the user chose, used to reuse an open draft."""
    canonical = json.dumps(
        {
            "uid": uid,
            "offer_id": body.offer_id,
            "passengers": [p.model_dump() for p in body.passengers],
            "contact": {"email": body.contact_email.lower(), "phone": body.contact_phone},
            "seats": sorted(
                [sp.segment_key, sp.passenger_index, sp.seat_id_local]
                for sp in body.seat_preferences
            ),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_expired(iso_ts: Optional[str]) -> bool:
    if not iso_ts:
        return False
    try:
        return datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except ValueError:
        return False


def _find_reusable_draft(db, uid: str, fingerprint: str, amount: str, currency: str) -> Optional[dict]:
    """An identical, still-payable draft at the same live price, if one exists.

    Going Back and checking out again would otherwise create a new booking
    each time. Reusing the draft also reuses its PaymentIntent, so a payment
    that already succeeded is caught (409) instead of being charged twice.
    """
    try:
        query = (
            db.collection("bookings")
            .where("uid", "==", uid)
            .where("draft_fingerprint", "==", fingerprint)
        )
        for snap in query.stream():
            doc = snap.to_dict() or {}
            if (
                doc.get("status") == _DRAFT_STATUS
                and str(doc.get("amount")) == str(amount)
                and str(doc.get("currency") or "").upper() == str(currency).upper()
                and not _is_expired(doc.get("offer_expires_at"))
            ):
                return doc
    except Exception as exc:
        # Best-effort: fall back to creating a new draft.
        logger.warning("Draft reuse lookup failed: %s", type(exc).__name__)
    return None


def _load_owned_booking(db, booking_id: str, uid: str) -> dict:
    try:
        snap = db.collection("bookings").document(booking_id).get()
    except Exception as exc:
        logger.error("Failed to load booking: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch booking.",
        )

    if not snap.exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found"
        )

    data = snap.to_dict() or {}
    if data.get("uid") != uid:
        logger.warning(
            "Booking ownership mismatch booking_id=%s requester_uid=%s",
            booking_id, uid,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found"
        )
    data["booking_id"] = data.get("booking_id") or booking_id
    return data


@router.post(
    "/draft",
    response_model=BookingDraftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a booking draft with server-validated pricing",
)
async def create_booking_draft(
    body: BookingDraftRequest,
    uid: Annotated[str, Depends(verified_uid)],
    duffel: Annotated[DuffelService, Depends(get_duffel_service)],
    db: Annotated[object, Depends(get_bookings_db)],
) -> BookingDraftResponse:
    # Round-trip drafts are unsafe until the frontend switches to a single
    # multi-slice Duffel offer. Do not combine two browser-supplied offers.
    if body.return_offer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Round-trip bookings must be represented by one Duffel offer "
                "with multiple slices. Two separate one-way offers cannot be "
                "combined into a payable draft."
            ),
        )

    # Revalidate the offer with Duffel to obtain authoritative pricing.
    try:
        offer = await duffel.get_offer(body.offer_id)
    except DuffelOfferNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Offer is no longer available. Please search again.",
        )
    except DuffelServiceError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not validate the offer with our flight provider.",
        )

    if not isinstance(offer, dict) or not offer:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid offer payload from provider.",
        )

    amount = offer.get("total_amount")
    currency = offer.get("total_currency")
    if not amount or not currency:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Offer is missing pricing data.",
        )

    offer_passenger_types = _passenger_types(offer)
    if len(offer_passenger_types) != len(body.passengers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Passenger count mismatch: offer expects "
                f"{len(offer_passenger_types)}, request has {len(body.passengers)}."
            ),
        )

    slices = offer.get("slices") or []
    slice_count = len(slices)
    if slice_count == 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Offer has no itinerary slices.",
        )

    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    fingerprint = _draft_fingerprint(uid, body)

    existing = _find_reusable_draft(db, uid, fingerprint, str(amount), str(currency))
    if existing:
        logger.info(
            "Reusing identical booking draft booking_id=%s uid=%s",
            existing.get("booking_id"), uid,
        )
        return BookingDraftResponse(
            booking_id=existing["booking_id"],
            status=_DRAFT_STATUS,
            amount=str(existing.get("amount")),
            currency=str(existing.get("currency")).upper(),
            offer_expires_at=existing.get("offer_expires_at"),
            slice_count=int(existing.get("slice_count") or slice_count),
        )

    doc = {
        "booking_id": booking_id,
        "uid": uid,
        "status": _DRAFT_STATUS,
        "offer_id": body.offer_id,
        "amount": str(amount),
        "currency": str(currency).upper(),
        "offer_expires_at": offer.get("expires_at"),
        "slice_count": slice_count,
        "passenger_count": len(body.passengers),
        "passengers": [p.model_dump() for p in body.passengers],
        "contact": {"email": body.contact_email, "phone": body.contact_phone},
        # Display-only fields extracted from the offer; safe to expose later.
        "flight_summary": _extract_flight_summary(offer),
        # Full itinerary for My Trips; refreshed from the Duffel order on confirm.
        "itinerary": extract_itinerary(offer),
        # Fare breakdown and conditions; references and tickets are added on confirm.
        "order_details": extract_order_details(offer),
        "draft_fingerprint": fingerprint,
        # Local UI seat picks. Explicitly flagged as NOT confirmed with Duffel.
        "seat_preferences": [
            {**sp.model_dump(), "confirmed_with_duffel": False}
            for sp in body.seat_preferences
        ],
        "created_at": now,
        "updated_at": now,
    }

    try:
        db.collection("bookings").document(booking_id).set(doc)
    except Exception as exc:
        logger.error("Failed to persist booking draft: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist booking draft.",
        )

    logger.info(
        "Booking draft created booking_id=%s uid=%s slice_count=%d passenger_count=%d",
        booking_id, uid, slice_count, len(body.passengers),
    )

    return BookingDraftResponse(
        booking_id=booking_id,
        status=_DRAFT_STATUS,
        amount=doc["amount"],
        currency=doc["currency"],
        offer_expires_at=doc["offer_expires_at"],
        slice_count=slice_count,
    )


@router.get(
    "",
    response_model=BookingsListResponse,
    summary="List all bookings owned by the authenticated user (newest first)",
)
async def list_bookings(
    uid: Annotated[str, Depends(verified_uid)],
    db: Annotated[object, Depends(get_bookings_db)],
) -> BookingsListResponse:
    base = db.collection("bookings").where("uid", "==", uid)
    try:
        try:
            # Needs the composite index in firestore.indexes.json (uid ASC, created_at DESC).
            ordered = base.order_by("created_at", direction="DESCENDING").limit(MAX_LISTED_BOOKINGS)
            docs = [snap.to_dict() or {} for snap in ordered.stream()]
        except FailedPrecondition:
            logger.warning(
                "Firestore index bookings(uid ASC, created_at DESC) is missing; "
                "using an unordered query. Deploy firestore.indexes.json."
            )
            docs = [snap.to_dict() or {} for snap in base.stream()]
    except Exception as exc:
        logger.error("Failed to list bookings: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list bookings.",
        )

    # Belt-and-braces sort — never rely on the DB layer alone.
    docs.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    summaries = [_to_summary(d) for d in docs[:MAX_LISTED_BOOKINGS]]
    return BookingsListResponse(bookings=summaries, count=len(summaries))


@router.get(
    "/{booking_id}/details",
    response_model=BookingDetails,
    summary="Trip details for My Trips: itinerary, travellers, payment history",
)
async def get_booking_details(
    booking_id: str,
    uid: Annotated[str, Depends(verified_uid)],
    db: Annotated[object, Depends(get_bookings_db)],
) -> BookingDetails:
    data = _load_owned_booking(db, booking_id, uid)
    return _to_details(data, list_payment_records(db, data["booking_id"]))


@router.get(
    "/{booking_id}",
    response_model=BookingSummary,
    summary="Fetch a single booking owned by the authenticated user",
)
async def get_booking(
    booking_id: str,
    uid: Annotated[str, Depends(verified_uid)],
    db: Annotated[object, Depends(get_bookings_db)],
) -> BookingSummary:
    return _to_summary(_load_owned_booking(db, booking_id, uid))
