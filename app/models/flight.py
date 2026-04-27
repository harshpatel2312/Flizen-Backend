from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class Passengers(BaseModel):
    """Passenger count breakdown"""
    adults: int
    children: int = 0
    infants: int = 0

class FlightSearchRequest(BaseModel):
    """Flight search request model"""
    from_airport: str  # IATA code
    to_airport: str    # IATA code
    departure_date: str  # YYYY-MM-DD
    return_date: Optional[str] = None  # YYYY-MM-DD (null for one-way)
    passengers: Passengers
    cabin_class: str  # Economy, Premium Economy, Business, First

class Flight(BaseModel):
    """Individual flight model"""
    id: str
    airline: str
    airline_code: str
    flight_number: str
    from_airport: str
    to_airport: str
    departure_time: str
    arrival_time: str
    duration: str
    stops: int
    price: float
    cabin_class: str
    aircraft: str

class FlightSearchResponse(BaseModel):
    """Flight search response model"""
    outbound_flights: List[Flight]
    return_flights: Optional[List[Flight]] = None
    search_date: str
    currency: str = "USD"
