"""Payment records stored under bookings/{booking_id}/payments/{payment_intent_id}.

One document per Stripe PaymentIntent. Written only by the backend (when a
PaymentIntent is created) and by signature-verified Stripe webhooks. Keying by
the PaymentIntent id means client retries and webhook replays update the same
record instead of creating duplicates.

Only non-secret metadata is stored: Stripe ids, amounts, currency, statuses,
and timestamps. Client secrets and card data are never written.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

BOOKINGS = "bookings"
PAYMENTS = "payments"

# Statuses a payment record can hold (Stripe PaymentIntent statuses plus refunds).
PAYMENT_FAILED = "payment_failed"
SUCCEEDED = "succeeded"
CANCELED = "canceled"
PARTIALLY_REFUNDED = "partially_refunded"
REFUNDED = "refunded"

_STATUS_TIMESTAMP = {
    SUCCEEDED: "succeeded_at",
    PAYMENT_FAILED: "failed_at",
    CANCELED: "canceled_at",
    PARTIALLY_REFUNDED: "refunded_at",
    REFUNDED: "refunded_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def can_transition(current: Optional[str], new: str) -> bool:
    """Webhooks can arrive late or out of order; never move a record backwards."""
    if not current or current == new:
        return True
    if current in (REFUNDED, CANCELED):
        return False
    if current == PARTIALLY_REFUNDED:
        return new == REFUNDED
    if current == SUCCEEDED:
        return new in (PARTIALLY_REFUNDED, REFUNDED)
    # requires_payment_method / requires_action / processing / payment_failed
    return True


def payment_ref(db, booking_id: str, payment_intent_id: str):
    return (
        db.collection(BOOKINGS).document(booking_id)
        .collection(PAYMENTS).document(payment_intent_id)
    )


def record_payment(
    db,
    *,
    booking_id: Optional[str],
    payment_intent_id: Optional[str],
    uid: Optional[str] = None,
    status: Optional[str] = None,
    **fields: Any,
) -> None:
    """Create or update a payment record. Best-effort: logs and never raises."""
    if not booking_id or not payment_intent_id:
        return
    try:
        ref = payment_ref(db, booking_id, payment_intent_id)
        snap = ref.get()
        current = (snap.to_dict() or {}) if snap.exists else {}
        now = _now()

        update = {k: v for k, v in fields.items() if v is not None}
        update.update({
            "payment_intent_id": payment_intent_id,
            "booking_id": booking_id,
            "updated_at": now,
        })
        if uid:
            update["uid"] = uid
        if not current:
            update["created_at"] = now
        if status and can_transition(current.get("status"), status):
            update["status"] = status
            ts_field = _STATUS_TIMESTAMP.get(status)
            if ts_field and not current.get(ts_field):
                update[ts_field] = now
        ref.set(update, merge=True)
    except Exception as exc:
        logger.error(
            "Failed to record payment booking_id=%s: %s", booking_id, type(exc).__name__,
        )


def list_payment_records(db, booking_id: str) -> list[dict]:
    """All payment records for a booking, oldest first."""
    try:
        docs = [
            snap.to_dict() or {}
            for snap in db.collection(BOOKINGS).document(booking_id).collection(PAYMENTS).stream()
        ]
    except Exception as exc:
        logger.error(
            "Failed to list payments booking_id=%s: %s", booking_id, type(exc).__name__,
        )
        return []
    docs.sort(key=lambda d: d.get("created_at") or "")
    return docs


def find_booking_id_by_payment_intent(db, payment_intent_id: Optional[str]) -> Optional[str]:
    """Resolve a booking from a PaymentIntent id (for events without our metadata)."""
    if not payment_intent_id:
        return None
    try:
        query = db.collection(BOOKINGS).where("payment_intent_id", "==", payment_intent_id).limit(1)
        for snap in query.stream():
            data = snap.to_dict() or {}
            return data.get("booking_id")
    except Exception as exc:
        logger.error("Booking lookup by PaymentIntent failed: %s", type(exc).__name__)
    return None
