from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import logging

from app.db.database import get_db
from app.schemas.auth import UserCreate, UserResponse
from app.services import auth_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Registers a new user and returns the public identity fields."
)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    try:
        user = auth_service.create_user(db, user_in)
        db.commit()
        return user
    except ValueError as e:
        # Expected for oversized passwords
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except IntegrityError:
        # Expected for duplicate emails
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Error during registration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
