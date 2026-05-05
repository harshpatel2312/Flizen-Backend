from pydantic import BaseModel
from typing import Optional


class AirportResponse(BaseModel):
    """Airport response model for API"""
    id: str
    name: str
    city: str
    country: str
    iata: str
    icao: Optional[str] = None
    latitude: float
    longitude: float
    timezone: Optional[str] = None
