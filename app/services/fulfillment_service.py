"""Fulfillment pipeline for Stripe → Duffel one-way bookings.

Runs from the Stripe webhook. All state transitions are guarded by
- the `stripe_events/{event_id}` document, which is created atomically
  (Firestore `.create()` fails if it already exists) and provides
  event-level idempotency;
- the booking's `status` field, checked before we ever hit Duffel, which
  prevents duplicate Duffel orders even if Stripe re-delivers with a new
  event id.

A confirmed flight booking is written ONLY after Duffel returns 201 with
an order id and booking reference.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.services.duffel_service import (
    DuffelOfferNotFoundError,
    DuffelServiceError,
)
from app.services.stripe_service import StripeServiceError

logger = get_logger(__name__)


# Booking status vocabulary used by this module and by the tests.
STATUS_DRAFT              = "draft_awaiting_payment"
STATUS_PAYMENT_RECEIVED   = "payment_received"
STATUS_FULFILLING         = "fulfilling"
STATUS_CONFIRMED          = "confirmed"
STATUS_PAYMENT_FAILED     = "payment_failed"
STATUS_CANCELED           = "canceled"
STATUS_FAILED_REFUNDING   = "failed_refunding"
STATUS_REFUNDED           = "refunded"

_PROCEED_STATUSES = {STATUS_DRAFT, STATUS_PAYMENT_RECEIVED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(obj: Any, key: str, default=None):
    """Access .attr or [key] from a Stripe Event / dict interchangeably."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_booking_id(stripe_object: Any) -> Optional[str]:
    md = _get(stripe_object, "metadata") or {}
    if hasattr(md, "get"):
        return md.get("flizen_booking_id")
    return None


def _map_gender(g: Optional[str]) -> str:
    if not g:
        return "u"
    g = g.strip().lower()
    if g in ("m", "male"):   return "m"
    if g in ("f", "female"): return "f"
    return "u"


def _derive_title(g: Optional[str]) -> str:
    return "mr" if _map_gender(g) == "m" else "ms"


def _build_duffel_passengers(offer: dict, stored_passengers: list, contact: dict) -> list:
    offer_pax = offer.get("passengers") or []
    result = []
    for stored, op in zip(stored_passengers, offer_pax):
        result.append({
            "id":            op.get("id"),
            "given_name":    stored.get("given_name"),
            "family_name":   stored.get("family_name"),
            "born_on":       stored.get("born_on"),
            "gender":        _map_gender(stored.get("gender")),
            "title":         _derive_title(stored.get("gender")),
            "email":         (contact or {}).get("email"),
            "phone_number":  (contact or {}).get("phone") or "",
        })
    return result


def _acquire_event_lock(db, event_id: str, event_type: str) -> bool:
    """Atomic-ish idempotency lock via document.create().

    Returns True if we won the lock; False if the event was already processed.
    """
    ref = db.collection("stripe_events").document(event_id)
    try:
        ref.create({
            "id":         event_id,
            "type":       event_type,
            "received_at": _now(),
        })
        return True
    except Exception:
        # AlreadyExists (real Firestore) or our fake's equivalent.
        return False


async def process_event(event: Any, *, db, duffel_svc, stripe_svc) -> dict:
    event_id   = _get(event, "id")
    event_type = _get(event, "type")
    data       = _get(event, "data") or {}
    obj        = _get(data, "object") or {}

    if not event_id or not event_type:
        return {"status": "malformed_event"}

    if not _acquire_event_lock(db, event_id, event_type):
        logger.info("Duplicate webhook event event_id=%s", event_id)
        return {"status": "duplicate"}

    booking_id = _extract_booking_id(obj)
    if not booking_id:
        logger.warning(
            "Webhook event has no booking metadata event_id=%s type=%s",
            event_id, event_type,
        )
        return {"status": "no_booking"}

    booking_ref = db.collection("bookings").document(booking_id)
    snap = booking_ref.get()
    if not snap.exists:
        return {"status": "booking_not_found"}
    booking = snap.to_dict() or {}
    booking["booking_id"] = booking.get("booking_id") or booking_id

    if event_type == "payment_intent.succeeded":
        return await _handle_pi_succeeded(booking, booking_ref, obj, duffel_svc, stripe_svc)
    if event_type == "payment_intent.payment_failed":
        return _handle_pi_failed(booking, booking_ref)
    if event_type == "payment_intent.canceled":
        return _handle_pi_canceled(booking, booking_ref)
    if event_type == "charge.refunded":
        return _handle_charge_refunded(booking, booking_ref)

    return {"status": "unhandled_event_type", "event_type": event_type}


async def _handle_pi_succeeded(booking, booking_ref, obj, duffel_svc, stripe_svc) -> dict:
    current = booking.get("status")
    if current not in _PROCEED_STATUSES:
        # Something already advanced the booking (fulfilling/confirmed/failed).
        # Do NOT re-attempt Duffel order creation.
        logger.info(
            "PI succeeded but booking status=%s — not re-fulfilling booking_id=%s",
            current, booking.get("booking_id"),
        )
        return {"status": "already_advanced", "current_status": current}

    # Transition to fulfilling to block any concurrent event from also creating
    # a Duffel order. In production this is safer wrapped in a Firestore
    # transaction; sequential correctness is enforced by the event-id lock above.
    booking_ref.set({
        "status":            STATUS_FULFILLING,
        "payment_status":    "succeeded",
        "stripe_charge_id":  _get(obj, "latest_charge"),
        "payment_received_at": _now(),
        "updated_at":        _now(),
    }, merge=True)

    offer_id = booking.get("offer_id")
    if not offer_id:
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="missing_offer_id",
        )

    try:
        offer = await duffel_svc.get_offer(offer_id)
    except DuffelOfferNotFoundError:
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="offer_gone",
        )
    except DuffelServiceError:
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="offer_revalidation_failed",
        )

    live_amount   = str(offer.get("total_amount")   or "")
    live_currency = str(offer.get("total_currency") or "").upper()
    if (
        live_amount != str(booking.get("amount"))
        or live_currency != str(booking.get("currency")).upper()
    ):
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="offer_price_changed",
        )

    stored_pax = booking.get("passengers") or []
    offer_pax  = offer.get("passengers") or []
    if len(stored_pax) != len(offer_pax):
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="passenger_count_mismatch",
        )

    duffel_passengers = _build_duffel_passengers(offer, stored_pax, booking.get("contact") or {})
    idem_key = f"flizen-order-{booking.get('booking_id')}"

    try:
        order = await duffel_svc.create_order(
            offer_id=offer_id,
            amount=booking.get("amount"),
            currency=booking.get("currency"),
            passengers=duffel_passengers,
            idempotency_key=idem_key,
        )
    except DuffelServiceError:
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="duffel_order_creation_failed",
        )

    order_id           = order.get("id")
    booking_reference  = order.get("booking_reference")
    if not order_id or not booking_reference:
        return await _mark_failed_and_refund(
            booking, booking_ref, stripe_svc, reason="duffel_order_missing_identifiers",
        )

    booking_ref.set({
        "status":                    STATUS_CONFIRMED,
        "duffel_order_id":           order_id,
        "duffel_booking_reference":  booking_reference,
        "confirmed_at":              _now(),
        "updated_at":                _now(),
    }, merge=True)
    logger.info(
        "Booking confirmed booking_id=%s duffel_order_id=%s",
        booking.get("booking_id"), order_id,
    )
    return {"status": "confirmed", "duffel_order_id": order_id}


async def _mark_failed_and_refund(booking, booking_ref, stripe_svc, *, reason: str) -> dict:
    booking_id = booking.get("booking_id")
    payment_intent_id = booking.get("payment_intent_id")

    booking_ref.set({
        "status":         STATUS_FAILED_REFUNDING,
        "failure_reason": reason,
        "updated_at":     _now(),
    }, merge=True)

    if not payment_intent_id:
        booking_ref.set({"refund_state": "not_applicable", "updated_at": _now()}, merge=True)
        logger.warning(
            "Booking failed with no payment_intent_id booking_id=%s reason=%s",
            booking_id, reason,
        )
        return {"status": "failed_no_refund", "reason": reason}

    try:
        stripe_svc.create_refund(
            payment_intent_id=payment_intent_id,
            idempotency_key=f"flizen-refund-{booking_id}",
            reason="requested_by_customer",
        )
        booking_ref.set({
            "refund_state":         "initiated",
            "refund_initiated_at":  _now(),
            "updated_at":           _now(),
        }, merge=True)
        logger.info(
            "Refund initiated booking_id=%s reason=%s", booking_id, reason,
        )
        return {"status": "failed_refund_initiated", "reason": reason}
    except StripeServiceError:
        booking_ref.set({
            "refund_state": "failed_to_initiate",
            "updated_at":   _now(),
        }, merge=True)
        logger.error(
            "Refund initiation failed booking_id=%s reason=%s", booking_id, reason,
        )
        return {"status": "failed_refund_failed", "reason": reason}


def _handle_pi_failed(booking, booking_ref) -> dict:
    if booking.get("status") in {STATUS_CONFIRMED, STATUS_FULFILLING}:
        # Never regress a confirmed / in-flight order.
        return {"status": "no_op_confirmed"}
    booking_ref.set({
        "status":         STATUS_PAYMENT_FAILED,
        "payment_status": "failed",
        "updated_at":     _now(),
    }, merge=True)
    return {"status": STATUS_PAYMENT_FAILED}


def _handle_pi_canceled(booking, booking_ref) -> dict:
    if booking.get("status") in {
        STATUS_CONFIRMED, STATUS_FULFILLING, STATUS_FAILED_REFUNDING, STATUS_REFUNDED,
    }:
        return {"status": "no_op_terminal"}
    booking_ref.set({
        "status":         STATUS_CANCELED,
        "payment_status": "canceled",
        "updated_at":     _now(),
    }, merge=True)
    return {"status": STATUS_CANCELED}


def _handle_charge_refunded(booking, booking_ref) -> dict:
    booking_ref.set({
        "status":       STATUS_REFUNDED,
        "refund_state": "completed",
        "refunded_at":  _now(),
        "updated_at":   _now(),
    }, merge=True)
    return {"status": STATUS_REFUNDED}
