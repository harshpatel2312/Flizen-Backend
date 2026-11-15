"""Pydantic models for booking-draft requests and responses.

Design rules:
- The request never carries price, currency, or offer expiry. Those are
  authoritative fields taken from Duffel server-side.
- `extra='forbid'` blocks any attempt by the client to smuggle
  price/amount/currency fields into a draft.
- Seat preferences are treated as UI hints only, never as Duffel seat
  service IDs.
"""
from datetime import date
from typing import List, Optional
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
_PHONE_FORMATTING_RE = re.compile(r"[\s\-().]")
# E.164 — the only phone format Duffel accepts when creating an order.
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone_e164(raw: Optional[str]) -> str:
    """Strip common formatting characters, e.g. '+1 (416) 555-1234' -> '+14165551234'."""
    return _PHONE_FORMATTING_RE.sub("", raw or "")


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PASSENGER_TYPES = {"adult", "child", "infant"}
GENDER_VALUES = {"male", "female", "unknown", "m", "f", "x"}


class BookingPassenger(BaseModel):
    """Minimum passenger fields required to draft a booking."""
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="adult")
    given_name: str
    family_name: str
    born_on: str = Field(..., description="Date of birth in YYYY-MM-DD")
    gender: str

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in PASSENGER_TYPES:
            raise ValueError(f"type must be one of {sorted(PASSENGER_TYPES)}")
        return v

    @field_validator("given_name", "family_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be empty")
        if len(v) > 100:
            raise ValueError("too long")
        return v

    @field_validator("born_on")
    @classmethod
    def _born_on(cls, v: str) -> str:
        v = (v or "").strip()
        if not _ISO_DATE_RE.match(v):
            raise ValueError("born_on must be YYYY-MM-DD")
        try:
            date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError("born_on is not a valid date") from exc
        return v

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in GENDER_VALUES:
            raise ValueError(f"gender must be one of {sorted(GENDER_VALUES)}")
        return v


class SeatPreference(BaseModel):
    """Local UI seat pick. Stored as a preference only, never sent to Duffel."""
    model_config = ConfigDict(extra="forbid")

    segment_key: str
    passenger_index: int = Field(..., ge=0)
    seat_id_local: str


class BookingDraftRequest(BaseModel):
    """Client-supplied fields for a booking draft.

    NOTE: no `amount`, `currency`, or `price` fields exist here by design.
    """
    model_config = ConfigDict(extra="forbid")

    offer_id: str
    # Present in the current UI's round-trip model. Rejected at the route until
    # a single multi-slice offer is used instead.
    return_offer_id: Optional[str] = None
    passengers: List[BookingPassenger] = Field(..., min_length=1, max_length=9)
    contact_email: str
    contact_phone: str
    seat_preferences: List[SeatPreference] = Field(default_factory=list)

    @field_validator("offer_id", "return_offer_id")
    @classmethod
    def _offer_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email")
        return v

    @field_validator("contact_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        v = normalize_phone_e164(v)
        if not _E164_RE.match(v):
            raise ValueError("phone must include country code, e.g. +14165551234")
        return v


class BookingDraftResponse(BaseModel):
    """Server-authoritative snapshot returned for POST /draft and GET /{id}."""
    booking_id: str
    status: str
    amount: str
    currency: str
    offer_expires_at: Optional[str] = None
    slice_count: int = 0


class FlightSummary(BaseModel):
    """Minimal display fields extracted from the Duffel offer at draft time.

    For round-trip offers (single Duffel offer with two slices), the return
    slice is exposed via `return_*` fields. Only display info, no PII.
    """
    from_airport:   Optional[str] = None
    to_airport:     Optional[str] = None
    airline:        Optional[str] = None
    departure_date: Optional[str] = None
    # Return slice — populated only for round-trip offers.
    return_from_airport:   Optional[str] = None
    return_to_airport:     Optional[str] = None
    return_departure_date: Optional[str] = None
    is_round_trip: bool = False
    airline_logo_url: Optional[str] = None


# Flight schedule stored on the booking. Times are airport-local as returned
# by Duffel (no UTC offset); display-only, no passenger data or prices.

class SegmentBaggage(BaseModel):
    type:     Optional[str] = None   # "checked" | "carry_on" (as returned by Duffel)
    quantity: int = 0


class SegmentPassenger(BaseModel):
    passenger_index: int
    cabin:    Optional[str] = None
    seat:     Optional[str] = None   # only when booked with the airline
    baggages: List[SegmentBaggage] = Field(default_factory=list)


class ItinerarySegment(BaseModel):
    origin:            Optional[str] = None
    origin_city:       Optional[str] = None
    origin_name:       Optional[str] = None
    origin_terminal:   Optional[str] = None
    origin_time_zone:  Optional[str] = None
    destination:       Optional[str] = None
    destination_city:  Optional[str] = None
    destination_name:  Optional[str] = None
    destination_terminal:  Optional[str] = None
    destination_time_zone: Optional[str] = None
    departing_at:      Optional[str] = None
    arriving_at:       Optional[str] = None
    duration:          Optional[str] = None
    airline:           Optional[str] = None
    airline_code:      Optional[str] = None
    airline_logo_url:  Optional[str] = None
    flight_number:     Optional[str] = None
    operating_airline: Optional[str] = None
    aircraft:          Optional[str] = None
    cabin:             Optional[str] = None
    travellers:        List[SegmentPassenger] = Field(default_factory=list)


class ItinerarySlice(BaseModel):
    origin:           Optional[str] = None
    origin_city:      Optional[str] = None
    destination:      Optional[str] = None
    destination_city: Optional[str] = None
    departing_at:     Optional[str] = None
    arriving_at:      Optional[str] = None
    duration:         Optional[str] = None
    stops:            int = 0
    fare_brand:       Optional[str] = None
    segments:         List[ItinerarySegment] = Field(default_factory=list)


class Itinerary(BaseModel):
    airline:          Optional[str] = None
    airline_logo_url: Optional[str] = None
    slices:           List[ItinerarySlice] = Field(default_factory=list)


class BookingSummary(BaseModel):
    """Response shape for GET /api/bookings and GET /api/bookings/{id}.

    Deliberately excludes: payment_intent_id, stripe_charge_id, payment
    amount in minor units, passenger PII (names, DOB, passport), contact
    details, offer_id, and seat preferences.
    """
    booking_id: str
    status: str
    amount: str
    currency: str
    slice_count: int = 0
    passenger_count: int = 0
    # High-level lifecycle signals — safe to expose.
    payment_status: Optional[str] = None
    refund_state:   Optional[str] = None
    failure_reason: Optional[str] = None
    # Populated ONLY when Duffel has confirmed the order.
    duffel_booking_reference: Optional[str] = None
    flight_summary: Optional[FlightSummary] = None
    # Null for bookings created before itineraries were stored.
    itinerary:     Optional[Itinerary] = None
    created_at:    Optional[str] = None
    updated_at:    Optional[str] = None
    confirmed_at:  Optional[str] = None
    offer_expires_at: Optional[str] = None


class BookingsListResponse(BaseModel):
    bookings: list[BookingSummary]
    count: int


# ─── Trip details (GET /api/bookings/{id}/details) ──────────────────────────
# Owner-only view used by My Trips. Adds traveller names, payment history,
# Duffel fare details, and the Duffel order id on top of the summary. Still
# excludes Stripe ids, client secrets, dates of birth, passport data, phone
# numbers, and seat ids.

class TravellerSummary(BaseModel):
    given_name:  str = ""
    family_name: str = ""
    type:        str = "adult"


class PaymentRecordSummary(BaseModel):
    status:          Optional[str] = None
    amount:          Optional[str] = None
    currency:        Optional[str] = None
    amount_refunded: Optional[str] = None
    refund_status:   Optional[str] = None
    failure_code:    Optional[str] = None
    created_at:      Optional[str] = None
    succeeded_at:    Optional[str] = None
    failed_at:       Optional[str] = None
    canceled_at:     Optional[str] = None
    refunded_at:     Optional[str] = None


class FareCondition(BaseModel):
    allowed:          bool = False
    penalty_amount:   Optional[str] = None
    penalty_currency: Optional[str] = None


class FareConditions(BaseModel):
    refund_before_departure: Optional[FareCondition] = None
    change_before_departure: Optional[FareCondition] = None


class OrderService(BaseModel):
    type:     Optional[str] = None   # "seat" | "baggage" | ...
    quantity: int = 1
    amount:   Optional[str] = None
    currency: Optional[str] = None


class AirlineReference(BaseModel):
    reference:        Optional[str] = None
    airline:          Optional[str] = None
    airline_code:     Optional[str] = None
    airline_logo_url: Optional[str] = None
    conditions_of_carriage_url: Optional[str] = None


class TicketSummary(BaseModel):
    passenger_index: int
    number:          Optional[str] = None


class OrderDetails(BaseModel):
    """Duffel fare breakdown, extras, conditions, references, and tickets (owner-only)."""
    base_amount:  Optional[str] = None
    tax_amount:   Optional[str] = None
    total_amount: Optional[str] = None
    currency:     Optional[str] = None
    services:     List[OrderService] = Field(default_factory=list)
    conditions:   Optional[FareConditions] = None
    airline_references: List[AirlineReference] = Field(default_factory=list)
    tickets:      List[TicketSummary] = Field(default_factory=list)


class BookingDetails(BookingSummary):
    travellers:           List[TravellerSummary] = Field(default_factory=list)
    order_details:        Optional[OrderDetails] = None
    # Shown to the owner in the "Additional information" section of Trip Details.
    duffel_order_id:      Optional[str] = None
    contact_email:        Optional[str] = None
    payments:             List[PaymentRecordSummary] = Field(default_factory=list)
    payment_received_at:  Optional[str] = None
    payment_failed_at:    Optional[str] = None
    canceled_at:          Optional[str] = None
    refund_initiated_at:  Optional[str] = None
    refunded_at:          Optional[str] = None
