from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import verified_uid
from app.core.firebase import get_db
from app.core.logging import get_logger
from app.models.profile import ProfileData

logger = get_logger(__name__)
router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", summary="Fetch the authenticated user's profile")
async def get_profile(uid: Annotated[str, Depends(verified_uid)]):
    """Return the stored profile document for the requesting user."""
    try:
        doc = get_db().collection("users").document(uid).get()
        return doc.to_dict() if doc.exists else {}
    except Exception as exc:
        logger.error("Error fetching profile uid=%s: %s", uid, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch profile")


@router.put("", summary="Save the authenticated user's profile")
async def save_profile(
    body: ProfileData,
    uid: Annotated[str, Depends(verified_uid)],
):
    """Persist (merge) the profile document for the requesting user."""
    # Exclude unset/None values so we never overwrite existing Firestore fields with null.
    data: dict = body.model_dump(exclude_none=True)
    data["updatedAt"] = datetime.now(timezone.utc).isoformat()

    try:
        get_db().collection("users").document(uid).set(data, merge=True)
        logger.info("Profile saved uid=%s", uid)
        return {"success": True}
    except Exception as exc:
        logger.error("Error saving profile uid=%s: %s", uid, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save profile")
