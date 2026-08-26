"""
Flight schema models for request/response handling
"""
from pydantic import BaseModel, Field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Duffel Flight Models
# ---------------------------------------------------------------------------

class SegmentInfo(BaseModel):
    """One aircraft leg within a flight itinerary"""
    airline: str
    flight_number: str
    origin: str
    destination: str
    departure_datetime: str
    arrival_datetime: str
    duration: Optional[str] = None
    aircraft_name: Optional[str] = None


class FlightCondition(BaseModel):
    """Refund / change condition from Duffel"""
    allowed: Optional[bool] = None
    penalty_amount: Optional[str] = None
    penalty_currency: Optional[str] = None


class FlightConditions(BaseModel):
    refund_before_departure: Optional[FlightCondition] = None
    change_before_departure: Optional[FlightCondition] = None


class BaggageAllowance(BaseModel):
    type: str = Field(..., description="checked or carry_on")
    quantity: int
    max_weight_kg: Optional[float] = None


class SliceSummary(BaseModel):
    """One slice of an offer (outbound or return). Segments are per-slice."""
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    departure_datetime: str
    arrival_datetime: str
    duration: Optional[str] = None
    stops: int = 0
    segments: List[SegmentInfo] = Field(default_factory=list)


class DuffelFlightOffer(BaseModel):
    """Flight offer formatted for frontend"""
    id: str = Field(..., description="Unique offer ID from Duffel")
    airline: str = Field(..., description="Airline name")
    airline_logo: Optional[str] = Field(None, description="Airline logo URL")
    flight_number: str = Field(..., description="Flight number")
    origin: str = Field(..., description="Origin airport IATA code")
    destination: str = Field(..., description="Destination airport IATA code")
    departure_time: str = Field(..., description="Departure time HH:MM")
    arrival_time: str = Field(..., description="Arrival time HH:MM")
    departure_datetime: str = Field(..., description="Full departure datetime ISO 8601")
    arrival_datetime: str = Field(..., description="Full arrival datetime ISO 8601")
    duration: str = Field(..., description="Flight duration ISO 8601 (e.g., PT1H35M)")
    stops: int = Field(default=0, ge=0, description="Number of connecting stops")
    price: str = Field(..., description="Total price as string")
    currency: str = Field(default="USD", description="Currency code")
    # Extended fields
    segments: List[SegmentInfo] = Field(default_factory=list, description="Individual flight legs (outbound slice; kept for backward compat)")
    slices: List[SliceSummary] = Field(default_factory=list, description="All itinerary slices — len==1 for one-way, len==2 for round-trip. Both share this offer id.")
    slice_count: int = Field(default=1, ge=1, description="Number of slices in this offer")
    cabin_class: Optional[str] = Field(None, description="Cabin class label")
    fare_brand_name: Optional[str] = Field(None, description="Fare brand / ticket type")
    conditions: Optional[FlightConditions] = Field(None, description="Refund / change conditions")
    baggages: List[BaggageAllowance] = Field(default_factory=list, description="Baggage allowances")
    total_emissions_kg: Optional[str] = Field(None, description="Estimated CO2 emissions in kg for all passengers")
    passport_required: Optional[bool] = Field(None, description="Whether identity documents (passport) are required for booking")


class DuffelFlightSearchResponse(BaseModel):
    """Duffel flight search response"""
    flights: List[DuffelFlightOffer] = Field(
        default_factory=list, description="List of available flights"
    )
    count: int = Field(default=0, description="Number of flights returned")


class DuffelFlightSearchError(BaseModel):
    """Error response"""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional error details")


# Legacy Models (kept for backward compatibility)

class FlightSegment(BaseModel):
    """Single flight segment (leg) details"""
    airline: str = Field(..., description="Airline name")
    airline_code: str = Field(..., description="Airline IATA code")
    flight_number: str = Field(..., description="Flight number")
    departure_time: str = Field(..., description="Departure time in HH:MM format")
    arrival_time: str = Field(..., description="Arrival time in HH:MM format")
    departure_airport: str = Field(..., description="Departure airport IATA code")
    arrival_airport: str = Field(..., description="Arrival airport IATA code")
    duration: str = Field(..., description="Flight duration (e.g., 1h 35m)")
    aircraft: Optional[str] = Field(None, description="Aircraft type")


class FlightOffer(BaseModel):
    """Complete flight offer with all details"""
    id: str = Field(..., description="Unique flight ID")
    airline: str = Field(..., description="Operating airline name")
    flight_number: str = Field(..., description="Flight number")
    departure_time: str = Field(..., description="Departure time HH:MM")
    arrival_time: str = Field(..., description="Arrival time HH:MM")
    duration: str = Field(..., description="Total flight duration")
    stops: int = Field(..., ge=0, description="Number of stops")
    price: float = Field(..., gt=0, description="Price in specified currency")
    currency: str = Field(..., description="Currency code (e.g., CAD, USD)")
    segments: List[FlightSegment] = Field(default_factory=list, description="Flight segments")


class FlightSearchResponse(BaseModel):
    """Flight search response"""
    flights: List[FlightOffer] = Field(default_factory=list, description="List of available flights")
    count: int = Field(default=0, description="Number of flights returned")
    origin: str = Field(..., description="Origin airport code")
    destination: str = Field(..., description="Destination airport code")
    departure_date: str = Field(..., description="Search departure date")
    currency: str = Field(default="USD", description="Currency of prices")
    search_timestamp: str = Field(..., description="When the search was performed (ISO format)")


class FlightSearchError(BaseModel):
    """Error response"""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional error details")
    code: Optional[str] = Field(None, description="Error code")
