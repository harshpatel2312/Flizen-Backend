from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from app.services.duffel_service import DuffelService
from app.schemas.flight_schema import DuffelFlightSearchResponse
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["flights"])
duffel_service = DuffelService()


@router.get("/api/flights/search", response_model=DuffelFlightSearchResponse)
async def search_flights_duffel(
    origin: str = Query(..., description="Origin airport IATA code (e.g., YYZ)"),
    destination: str = Query(..., description="Destination airport IATA code (e.g., JFK)"),
    departure_date: str = Query(..., description="Departure date in YYYY-MM-DD format"),
    adults: int = Query(1, ge=1, description="Number of adult passengers"),
    children: int = Query(0, ge=0, description="Number of child passengers"),
    infants: int = Query(0, ge=0, description="Number of infant passengers"),
    cabin_class: str = Query("economy", description="Cabin class (economy, premium_economy, business, first)"),
    currency: Optional[str] = Query(None, description="Preferred currency code (e.g. USD, CAD, GBP)"),
):
    """
    Search for real flight offers using Duffel Air API
    
    Query Parameters:
    - origin: Departure airport code (e.g., YYZ)
    - destination: Arrival airport code (e.g., JFK)
    - departure_date: Date in YYYY-MM-DD format
    - adults: Number of adults (minimum 1, default 1)
    - children: Number of children (default 0)
    - infants: Number of infants (default 0)
    - cabin_class: Cabin class (default economy)
    
    Returns:
        List of flight offers with pricing and details
    
    Example:
        GET /api/flights/search?origin=YYZ&destination=JFK&departure_date=2026-05-20&adults=1&cabin_class=economy
    """
    try:
        logger.info(
            "Duffel flight search | %s → %s | %s | %s cabin | adults=%d children=%d infants=%d",
            origin, destination, departure_date, cabin_class, adults, children, infants,
        )
        # Validate origin and destination
        if not origin or len(origin) != 3 or not origin.isalpha():
            raise HTTPException(
                status_code=400,
                detail="Invalid origin airport code. Must be 3-letter IATA code."
            )
        
        if not destination or len(destination) != 3 or not destination.isalpha():
            raise HTTPException(
                status_code=400,
                detail="Invalid destination airport code. Must be 3-letter IATA code."
            )
        
        if origin.upper() == destination.upper():
            raise HTTPException(
                status_code=400,
                detail="Origin and destination cannot be the same airport."
            )
        
        # Validate date format
        try:
            departure_dt = datetime.strptime(departure_date, "%Y-%m-%d")
            if departure_dt < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
                raise HTTPException(
                    status_code=400,
                    detail="Departure date cannot be in the past."
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD."
            )
        
        # Validate passenger count
        total_passengers = adults + children + infants
        if total_passengers > 9:
            raise HTTPException(
                status_code=400,
                detail="Maximum 9 passengers allowed."
            )
        
        # Validate cabin class
        valid_cabin_classes = ["economy", "premium_economy", "business", "first"]
        if cabin_class.lower() not in valid_cabin_classes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cabin class. Must be one of: {', '.join(valid_cabin_classes)}"
            )
        
        # Search flights via Duffel
        flights = await duffel_service.search_flights(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
            currency=currency,
        )
        
        # Build response
        response = DuffelFlightSearchResponse(
            flights=flights,
            count=len(flights),
        )
        logger.info("Duffel search returned %d offers for %s → %s", len(flights), origin, destination)
        return response
    
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Flight search service is not properly configured. Please contact support."
        )
    except Exception as e:
        logger.error(f"Error in flight search: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while searching for flights."
        )
