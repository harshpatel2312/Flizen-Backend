from fastapi import APIRouter
from app.models.health import HealthResponse
from app.core.config import settings

router = APIRouter(tags=["health"])

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        Status and environment information
    """
    return HealthResponse(
        status="ok",
        environment=settings.ENVIRONMENT
    )
