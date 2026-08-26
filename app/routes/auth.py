from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from firebase_admin import auth
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    id_token: str


class UserResponse(BaseModel):
    uid: str
    email: str | None
    name: str | None
    picture: str | None


@router.post("/verify", response_model=UserResponse)
async def verify_token(payload: TokenRequest):
    """
    Verify a Firebase ID token received from the frontend.
    Returns the decoded user profile on success.
    """
    try:
        decoded = auth.verify_id_token(payload.id_token)
    except auth.ExpiredIdTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except auth.InvalidIdTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except Exception as exc:
        # Never log the raw token or a Firebase error message that may reference it.
        logger.error("Token verification failed: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token verification failed")

    logger.info("Authenticated user uid=%s email=%s", decoded.get("uid"), decoded.get("email"))

    return UserResponse(
        uid=decoded.get("uid"),
        email=decoded.get("email"),
        name=decoded.get("name"),
        picture=decoded.get("picture"),
    )
