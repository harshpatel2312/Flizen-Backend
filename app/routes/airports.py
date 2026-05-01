from fastapi import APIRouter, HTTPException, Query
from app.services.airport_service import AirportService
from app.models.airport import AirportResponse
from app.core.logging import get_logger
from typing import List

logger = get_logger(__name__)
router = APIRouter(prefix="/airports", tags=["airports"])
airport_service = AirportService()

@router.get("/search", response_model=List[AirportResponse])
async def search_airports(q: str = Query(..., min_length=1, max_length=100)):
    try:
        logger.debug("Airport search query: %s", q)
        results = airport_service.search_airports(q, limit=10)
        logger.debug("Airport search returned %d results for query '%s'", len(results), q)
        return results
    except Exception as e:
        logger.error("Error searching airports for query '%s': %s", q, e)
        raise HTTPException(status_code=500, detail=f"Error searching airports: {str(e)}")

@router.get("/{iata}", response_model=AirportResponse)
async def get_airport(iata: str):
    try:
        logger.debug("Fetching airport by IATA: %s", iata)
        airport = airport_service.get_airport_by_iata(iata)
        if not airport:
            logger.warning("Airport not found: %s", iata)
            raise HTTPException(status_code=404, detail=f"Airport with IATA code {iata} not found")
        return airport
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching airport '%s': %s", iata, e)
        raise HTTPException(status_code=500, detail=f"Error fetching airport: {str(e)}")
