from fastapi import APIRouter, HTTPException, Query
from app.services.airport_service import AirportService
from app.models.airport import AirportResponse
from typing import List

router = APIRouter(prefix="/airports", tags=["airports"])
airport_service = AirportService()

@router.get("/search", response_model=List[AirportResponse])
async def search_airports(q: str = Query(..., min_length=1, max_length=100)):
    """
    Search airports by city, name, IATA code, ICAO code, or country.
    
    Query parameters:
    - q: Search query string (required, 1-100 characters)
    
    Returns:
        List of matching airports with details (name, IATA, city, country, coordinates)
    """
    try:
        results = airport_service.search_airports(q, limit=10)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching airports: {str(e)}")

@router.get("/{iata}", response_model=AirportResponse)
async def get_airport(iata: str):
    """
    Get airport details by IATA code.
    
    Path parameters:
    - iata: Airport IATA code (e.g., JFK, LHR, YYZ)
    
    Returns:
        Airport details including coordinates and timezone
    """
    try:
        airport = airport_service.get_airport_by_iata(iata)
        if not airport:
            raise HTTPException(status_code=404, detail=f"Airport with IATA code {iata} not found")
        return airport
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching airport: {str(e)}")
