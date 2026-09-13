import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User
from app.db.models.user_profile import UserProfile
from app.schemas.profile import ProfileUpdate, ProfileResponse
from app.api.deps import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Get user profile",
    description="Returns the profile for the currently authenticated user."
)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    ).scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found"
        )
    return profile

@router.patch(
    "/profile",
    response_model=ProfileResponse,
    summary="Update user profile",
    description="Updates the profile for the currently authenticated user using partial semantics. Creates the profile if it does not exist."
)
def update_profile(
    profile_in: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    ).scalar_one_or_none()

    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)

    update_data = profile_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(profile, field, value)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

    return profile
