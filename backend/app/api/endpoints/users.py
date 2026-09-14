from fastapi import APIRouter, Depends
from app.schemas.auth import UserResponse
from app.db.models.user import User
from app.api.deps import get_current_user

router = APIRouter()

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Returns the currently authenticated user based on the session cookie."
)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
