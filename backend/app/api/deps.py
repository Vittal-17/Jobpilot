from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional

from app.db.database import get_db
from app.db.models.user import User
from app.services import auth_service
from app.core.security import SESSION_COOKIE_NAME

def get_current_user(
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db)
) -> User:
    """Dependency to retrieve the currently authenticated user from the session cookie."""
    unauthorized_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated"
    )

    if not session_token:
        raise unauthorized_exc

    session = auth_service.get_valid_session(db, session_token)
    if not session:
        raise unauthorized_exc

    user = db.execute(select(User).where(User.id == session.user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        raise unauthorized_exc

    return user
