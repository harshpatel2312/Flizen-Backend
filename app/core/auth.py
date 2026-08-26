"""Shared authentication dependencies for Firebase-issued bearer tokens."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as fb_auth

from app.core.logging import get_logger

logger = get_logger(__name__)

# HTTPBearer must be a module-level singleton so FastAPI reuses one security scheme.
_bearer = HTTPBearer(auto_error=True)


async def verified_uid(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> str:
    """Verify the incoming Firebase ID token and return its uid.

    Raises 401 on missing/expired/invalid tokens. Never logs the raw token.
    """
    try:
        decoded = fb_auth.verify_id_token(credentials.credentials)
    except fb_auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
        )
    except fb_auth.InvalidIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    except Exception as exc:
        # Log only the exception class, never the token itself.
        logger.error("Token verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
        )

    uid = decoded.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing uid"
        )
    return uid
