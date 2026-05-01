from fastapi import APIRouter
from app.models.health import HealthResponse
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

@router.get("/health", response_model=HealthResponse)
async def health_check():
    logger.debug("Health check requested")
    return HealthResponse(
        status="ok",
        environment=settings.ENVIRONMENT
    )
