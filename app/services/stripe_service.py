"""Stripe integration service.

Kept intentionally thin: the route layer owns business logic (ownership,
status checks, offer revalidation). This module owns Stripe SDK calls and
the currency-to-minor-units conversion.
"""
from decimal import Decimal, InvalidOperation
from typing import Optional

import stripe

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# Currencies that Stripe treats as zero-decimal (amount is the whole unit).
# Reference: https://stripe.com/docs/currencies#zero-decimal
ZERO_DECIMAL_CURRENCIES = frozenset({
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
})

# Three-decimal currencies have edge-case rounding rules on Stripe; we
# refuse them explicitly for now instead of guessing.
THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "JOD", "KWD", "OMR", "TND"})

# Statuses where an existing PaymentIntent can still be used by the client.
REUSABLE_STATUSES = frozenset({
    "requires_payment_method",
    "requires_confirmation",
    "requires_action",
    "processing",
})

TERMINAL_SUCCESS_STATUSES = frozenset({"succeeded"})
TERMINAL_DEAD_STATUSES = frozenset({"canceled"})


class StripeServiceError(Exception):
    """Stripe SDK returned an error we could not handle."""


class InvalidSignatureError(Exception):
    """Webhook signature verification failed."""


class UnsupportedCurrencyError(ValueError):
    """We refuse to bill in this currency (three-decimal / unknown)."""


class InvalidAmountError(ValueError):
    """The stored amount cannot be safely converted to Stripe minor units."""


def to_stripe_minor_units(amount: str, currency: str) -> int:
    """Convert a decimal-string amount into Stripe minor units.

    Refuses silent rounding: an amount with more precision than the currency
    allows raises `InvalidAmountError`.
    """
    if not isinstance(amount, str) or not amount:
        raise InvalidAmountError("amount must be a non-empty string")
    if not isinstance(currency, str) or len(currency) != 3:
        raise UnsupportedCurrencyError("currency must be a 3-letter code")

    currency_u = currency.upper()

    try:
        d = Decimal(amount)
    except InvalidOperation as exc:
        raise InvalidAmountError("amount is not a valid decimal") from exc

    if d <= 0:
        raise InvalidAmountError("amount must be positive")

    if currency_u in THREE_DECIMAL_CURRENCIES:
        raise UnsupportedCurrencyError(f"Currency {currency_u} is not supported yet")

    if currency_u in ZERO_DECIMAL_CURRENCIES:
        if d != d.to_integral_value():
            raise InvalidAmountError(
                f"Zero-decimal currency {currency_u} requires an integer amount"
            )
        return int(d)

    # Default: two-decimal currencies.
    q = d.quantize(Decimal("0.01"))
    if q != d:
        raise InvalidAmountError(
            f"Amount {amount} has more precision than {currency_u} allows"
        )
    return int((q * 100).to_integral_value())


class StripeService:
    """Thin wrapper so tests can inject a fake via FastAPI's dependency system."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key if api_key is not None else settings.STRIPE_SECRET_KEY

    def _configured(self):
        if not self._api_key:
            raise StripeServiceError("STRIPE_SECRET_KEY is not configured")
        # Set on the module — Stripe reads from stripe.api_key.
        stripe.api_key = self._api_key
        return stripe

    def create_payment_intent(
        self,
        *,
        amount_minor: int,
        currency: str,
        metadata: dict,
        idempotency_key: str,
    ):
        s = self._configured()
        try:
            return s.PaymentIntent.create(
                amount=amount_minor,
                currency=currency.lower(),
                metadata=metadata,
                automatic_payment_methods={"enabled": True},
                idempotency_key=idempotency_key,
            )
        except s.error.StripeError as exc:  # type: ignore[attr-defined]
            logger.error("Stripe PaymentIntent.create failed: %s", type(exc).__name__)
            raise StripeServiceError("Stripe PaymentIntent create failed") from exc

    def retrieve_payment_intent(self, intent_id: str):
        s = self._configured()
        try:
            return s.PaymentIntent.retrieve(intent_id)
        except s.error.StripeError as exc:  # type: ignore[attr-defined]
            logger.error("Stripe PaymentIntent.retrieve failed: %s", type(exc).__name__)
            raise StripeServiceError("Stripe PaymentIntent retrieve failed") from exc

    def cancel_payment_intent(self, intent_id: str):
        s = self._configured()
        try:
            return s.PaymentIntent.cancel(intent_id)
        except s.error.StripeError as exc:  # type: ignore[attr-defined]
            logger.error("Stripe PaymentIntent.cancel failed: %s", type(exc).__name__)
            raise StripeServiceError("Stripe PaymentIntent cancel failed") from exc

    def create_refund(
        self,
        *,
        payment_intent_id: str,
        idempotency_key: str,
        reason: str = "requested_by_customer",
    ):
        s = self._configured()
        try:
            return s.Refund.create(
                payment_intent=payment_intent_id,
                reason=reason,
                idempotency_key=idempotency_key,
            )
        except s.error.StripeError as exc:  # type: ignore[attr-defined]
            logger.error("Stripe Refund.create failed: %s", type(exc).__name__)
            raise StripeServiceError("Stripe refund failed") from exc

    def verify_webhook(self, payload: bytes, sig_header: str):
        """Verify a Stripe webhook signature against the raw request body."""
        secret = settings.STRIPE_WEBHOOK_SECRET
        if not secret:
            raise StripeServiceError("STRIPE_WEBHOOK_SECRET is not configured")
        try:
            return stripe.Webhook.construct_event(payload, sig_header, secret)
        except stripe.error.SignatureVerificationError as exc:  # type: ignore[attr-defined]
            raise InvalidSignatureError("Invalid Stripe signature") from exc
        except Exception as exc:
            # Malformed body etc.
            raise InvalidSignatureError("Malformed webhook payload") from exc
