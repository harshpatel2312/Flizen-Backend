"""Payment-related request/response models."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PaymentIntentRequest(BaseModel):
    """Client input for POST /api/payments/intents.

    Only `booking_id` is accepted. Amount and currency come from the server-side
    booking draft, not from the client.
    """
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(..., min_length=1)


class PaymentIntentResponse(BaseModel):
    """Response for the payments route.

    The client secret is required by Stripe Elements; we never log it.
    display_amount / currency mirror the booking draft so the UI can render
    a totals line without recomputing prices client-side.
    """
    booking_id: str
    client_secret: str
    display_amount: str
    currency: str
    status: Optional[str] = None
