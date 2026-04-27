from fastapi import APIRouter, HTTPException
from app.services.flight_service import FlightService
from app.models.flight import FlightSearchRequest, FlightSearchResponse

router = APIRouter(prefix="/flights", tags=["flights"])
flight_service = FlightService()

@router.post("/search", response_model=FlightSearchResponse)
async def search_flights(request: FlightSearchRequest):
    """
    Search for flights based on route, dates, passengers, and cabin class.
    
    Request body:
    - from_airport: Departure airport IATA code (e.g., JFK)
    - to_airport: Arrival airport IATA code (e.g., LHR)
    - departure_date: Departure date (YYYY-MM-DD)
    - return_date: Return date for round-trip (YYYY-MM-DD), null for one-way
    - passengers: Object with adults, children, infants counts
    - cabin_class: Cabin class (Economy, Premium Economy, Business, First)
    
    Returns:
        List of available flights for outbound and optional return flights
    """
    try:
        # Validate airports are different
        if request.from_airport.upper() == request.to_airport.upper():
            raise HTTPException(
                status_code=400,
                detail="From and To airports must be different"
            )
        
        # Validate at least 1 adult
        if request.passengers.adults < 1:
            raise HTTPException(
                status_code=400,
                detail="At least 1 adult passenger is required"
            )
        
        # Search flights
        results = flight_service.search_flights(request)
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching flights: {str(e)}")
