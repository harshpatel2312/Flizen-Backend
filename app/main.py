import argparse
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.routes import airports, flights, health, auth, profile, bookings, payments
from app.core.firebase import init_firebase

# ---------------------------------------------------------------------------
# Parse CLI arguments before anything else so logging is configured early.
# Usage:  python -m app.main --log-terminal
#         uvicorn app.main:app ...   (no terminal output unless flag given)
# ---------------------------------------------------------------------------
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument(
    "--log-terminal",
    action="store_true",
    default=False,
    help="Also emit log output to the terminal (stdout). Logs are always written to logs/flizen.log.",
)
_args, _ = _parser.parse_known_args()

configure_logging(
    log_to_terminal=_args.log_terminal,
    log_level=logging.DEBUG if settings.DEBUG else logging.INFO,
)

logger = get_logger(__name__)
logger.info("Logging initialised | terminal=%s | level=%s", _args.log_terminal, "DEBUG" if settings.DEBUG else "INFO")

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Flizen Flight Booking API",
    description="Premium flight booking platform API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.debug("CORS origins: %s", settings.CORS_ORIGINS)

# Initialise Firebase Admin SDK once at startup — all routes share the same app instance
init_firebase()

app.include_router(health.router)
app.include_router(airports.router)
app.include_router(flights.router)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(bookings.router)
app.include_router(payments.router)


@app.get("/")
async def root():
    return {
        "message": "Flizen Flight Booking API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
