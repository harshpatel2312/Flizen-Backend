"""POST /api/payments/intents — create or reuse a Stripe PaymentIntent.

Rules enforced here:
- Firebase auth required.
- Only the owner of the booking may create a PaymentIntent for it.
- Booking must be awaiting payment: a fresh draft, or a draft whose previous
  attempt was declined (Stripe keeps the PaymentIntent open for a retry).
- Amount + currency are read from the booking draft (server-side).
- Duffel offer is revalidated before creating a NEW intent, so we don't
  charge for a stale price.
- Metadata contains only booking_id, uid, offer_id — never passenger PII.
- Idempotency key covers (booking_id, amount, currency) so a retry with the
  same price returns the same intent.
- On refresh, an eligible existing PaymentIntent is reused.
- Every intent is mirrored to bookings/{id}/payments/{intent_id} (ids,
  amounts, status only — never the client secret).
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.auth import verified_uid
from app.core.logging import get_logger
from app.models.payment import PaymentIntentRequest, PaymentIntentResponse
from app.routes.bookings import get_bookings_db, get_duffel_service
from app.services import fulfillment_service
from app.services.booking_records import record_payment
from app.services.duffel_service import (
    DuffelOfferNotFoundError,
    DuffelService,
    DuffelServiceError,
)
from app.services.stripe_service import (
    InvalidAmountError,
    InvalidSignatureError,
    REUSABLE_STATUSES,
    StripeService,
    StripeServiceError,
    TERMINAL_DEAD_STATUSES,
    TERMINAL_SUCCESS_STATUSES,
    UnsupportedCurrencyError,
    to_stripe_minor_units,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/payments", tags=["payments"])

_PAYABLE_BOOKING_STATUSES = frozenset({"draft_awaiting_payment", "payment_failed"})

_stripe_service = StripeService()


def get_stripe_service() -> StripeService:
    return _stripe_service


def _idempotency_key(booking_id: str, amount_minor: int, currency: str) -> str:
    return f"booking:{booking_id}:{amount_minor}:{currency.upper()}"


def _sanitized_metadata(*, booking_id: str, uid: str, offer_id: str) -> dict:
    # Deliberately narrow: booking_id / uid / offer_id are safe identifiers.
    return {
        "flizen_booking_id": booking_id,
        "flizen_uid": uid,
        "flizen_offer_id": offer_id,
    }


@router.post(
    "/intents",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or reuse a Stripe PaymentIntent for a booking draft",
)
async def create_or_reuse_payment_intent(
    body: PaymentIntentRequest,
    uid: Annotated[str, Depends(verified_uid)],
    db: Annotated[object, Depends(get_bookings_db)],
    stripe_svc: Annotated[StripeService, Depends(get_stripe_service)],
    duffel: Annotated[DuffelService, Depends(get_duffel_service)],
) -> PaymentIntentResponse:
    # --- 1. Load booking & enforce ownership ---
    try:
        snap = db.collection("bookings").document(body.booking_id).get()
    except Exception as exc:
        logger.error("Failed to load booking for payment: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load booking.",
        )

    if not snap.exists:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking = snap.to_dict() or {}
    if booking.get("uid") != uid:
        logger.warning(
            "Payment ownership mismatch booking_id=%s requester=%s",
            body.booking_id, uid,
        )
        raise HTTPException(status_code=404, detail="Booking not found")

    booking_status = booking.get("status")
    if booking_status not in _PAYABLE_BOOKING_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Booking is not payable in its current state ({booking_status}).",
        )

    amount_str = booking.get("amount")
    currency = booking.get("currency")
    offer_id = booking.get("offer_id")
    if not amount_str or not currency or not offer_id:
        raise HTTPException(
            status_code=500,
            detail="Booking is missing pricing data.",
        )

    # --- 2. Compute Stripe minor units up-front (also validates currency) ---
    try:
        amount_minor = to_stripe_minor_units(str(amount_str), str(currency))
    except UnsupportedCurrencyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except InvalidAmountError as exc:
        raise HTTPException(status_code=500, detail=f"Bad stored amount: {exc}")

    # --- 3. Try to reuse an existing PaymentIntent ---
    existing_intent_id = booking.get("payment_intent_id")
    if existing_intent_id:
        try:
            existing = stripe_svc.retrieve_payment_intent(existing_intent_id)
        except StripeServiceError:
            existing = None

        if existing is not None:
            ex_status = getattr(existing, "status", None)
            ex_amount = int(getattr(existing, "amount", 0) or 0)
            ex_currency = (getattr(existing, "currency", "") or "").upper()
            ex_client_secret = getattr(existing, "client_secret", None)

            if ex_status in TERMINAL_SUCCESS_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail="This booking has already been paid.",
                )

            if (
                ex_status in REUSABLE_STATUSES
                and ex_amount == amount_minor
                and ex_currency == currency.upper()
                and ex_client_secret
            ):
                logger.info(
                    "Reusing PaymentIntent booking_id=%s status=%s",
                    body.booking_id, ex_status,
                )
                # Backfills a record for intents created before payment records existed.
                record_payment(
                    db, booking_id=body.booking_id, payment_intent_id=existing_intent_id,
                    uid=uid, status=ex_status, amount=str(amount_str),
                    amount_minor=ex_amount, currency=ex_currency,
                )
                return PaymentIntentResponse(
                    booking_id=body.booking_id,
                    client_secret=ex_client_secret,
                    display_amount=str(amount_str),
                    currency=currency.upper(),
                    status=ex_status,
                )

            # Price/currency changed OR status is dead — cancel if still cancellable,
            # then fall through to creating a new intent.
            if ex_status in REUSABLE_STATUSES:
                try:
                    stripe_svc.cancel_payment_intent(existing_intent_id)
                    record_payment(
                        db, booking_id=body.booking_id, payment_intent_id=existing_intent_id,
                        uid=uid, status="canceled",
                    )
                except StripeServiceError:
                    # Non-fatal — proceed to create a new intent regardless.
                    pass
            elif ex_status not in TERMINAL_DEAD_STATUSES:
                # Unknown state — refuse rather than double-charge.
                raise HTTPException(
                    status_code=409,
                    detail=f"Existing payment is in an unexpected state ({ex_status}).",
                )

    # --- 4. Revalidate the Duffel offer before creating a NEW intent ---
    try:
        offer = await duffel.get_offer(offer_id)
    except DuffelOfferNotFoundError:
        raise HTTPException(
            status_code=400,
            detail="Offer is no longer available. Please redo your search.",
        )
    except DuffelServiceError:
        raise HTTPException(
            status_code=502,
            detail="Could not validate the offer with our flight provider.",
        )

    live_amount = str(offer.get("total_amount") or "")
    live_currency = str(offer.get("total_currency") or "").upper()
    if live_amount != str(amount_str) or live_currency != currency.upper():
        raise HTTPException(
            status_code=409,
            detail="Price has changed since booking was drafted. Please redraft the booking.",
        )

    # --- 5. Create the PaymentIntent (idempotent on booking+amount+currency) ---
    metadata = _sanitized_metadata(
        booking_id=body.booking_id, uid=uid, offer_id=offer_id,
    )
    idem_key = _idempotency_key(body.booking_id, amount_minor, currency)

    try:
        intent = stripe_svc.create_payment_intent(
            amount_minor=amount_minor,
            currency=currency,
            metadata=metadata,
            idempotency_key=idem_key,
        )
    except StripeServiceError:
        raise HTTPException(status_code=502, detail="Payment provider error.")

    intent_id = getattr(intent, "id", None)
    intent_status = getattr(intent, "status", None)
    client_secret = getattr(intent, "client_secret", None)
    if not intent_id or not client_secret:
        raise HTTPException(status_code=502, detail="Malformed provider response.")

    # --- 6. Persist non-secret identifiers on the booking doc ---
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.collection("bookings").document(body.booking_id).set(
            {
                "payment_intent_id": intent_id,
                "payment_status": intent_status,
                "payment_amount_minor": amount_minor,
                "payment_currency": currency.upper(),
                "updated_at": now,
            },
            merge=True,
        )
    except Exception as exc:
        logger.error("Failed to persist payment intent id: %s", type(exc).__name__)
        # Don't raise — the intent already exists in Stripe. The idempotency key
        # will let a retry resolve to the same intent.

    record_payment(
        db, booking_id=body.booking_id, payment_intent_id=intent_id, uid=uid,
        status=intent_status, amount=str(amount_str), amount_minor=amount_minor,
        currency=currency.upper(),
    )

    logger.info(
        "PaymentIntent ready booking_id=%s intent_id=%s status=%s",
        body.booking_id, intent_id, intent_status,
    )

    return PaymentIntentResponse(
        booking_id=body.booking_id,
        client_secret=client_secret,
        display_amount=str(amount_str),
        currency=currency.upper(),
        status=intent_status,
    )


@router.post(
    "/webhook",
    include_in_schema=False,
    summary="Stripe webhook — signature-verified, idempotent fulfillment",
)
async def stripe_webhook(
    request: Request,
    db: Annotated[object, Depends(get_bookings_db)],
    stripe_svc: Annotated[StripeService, Depends(get_stripe_service)],
    duffel: Annotated[DuffelService, Depends(get_duffel_service)],
):
    # Raw body is REQUIRED for signature verification — do not use request.json().
    raw = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe_svc.verify_webhook(raw, sig)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except StripeServiceError:
        raise HTTPException(status_code=500, detail="Webhook is not configured")

    try:
        result = await fulfillment_service.process_event(
            event, db=db, duffel_svc=duffel, stripe_svc=stripe_svc,
        )
    except Exception as exc:
        # Return 500 so Stripe retries; never leak internal details.
        logger.error("Webhook processing crashed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Processing failed")

    return {"received": True, "result": result}
