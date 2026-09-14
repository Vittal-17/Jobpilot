from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.models.user import User
from app.db.models.user_session import UserSession
from app.core.security import (
    get_password_hash,
    verify_password,
    verify_dummy_password,
    generate_session_token,
    hash_session_token
)
from app.schemas.auth import UserCreate

SESSION_EXPIRY_DAYS = 30

def get_normalized_email(email: str) -> str:
    """Normalize email consistently with database unique constraint."""
    return email.strip().lower()

def create_user(db: Session, user_create: UserCreate) -> User:
    """Create a new user with hashed password. Leaves transaction commit to the caller."""
    normalized_email = get_normalized_email(user_create.email)
    hashed_pwd = get_password_hash(user_create.password)

    user = User(
        email=normalized_email,
        password_hash=hashed_pwd,
        display_name=user_create.display_name
    )
    db.add(user)
    db.flush()
    return user

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Authenticate user by email and password, mitigating timing attacks."""
    normalized_email = get_normalized_email(email)
    user = db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    ).scalar_one_or_none()

    if not user:
        # Dummy verification to prevent timing attacks
        verify_dummy_password(password)
        return None

    if not user.is_active:
        # Dummy verification
        verify_dummy_password(password)
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user

def create_session(db: Session, user_id: int) -> Tuple[UserSession, str]:
    """Create a new session. Leaves transaction commit to the caller."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRY_DAYS)

    session = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at
    )
    db.add(session)
    db.flush()
    return session, raw_token

def get_valid_session(db: Session, raw_token: str) -> Optional[UserSession]:
    """Lookup session by raw token. Return if valid and not expired or revoked. Does not commit."""
    token_hash = hash_session_token(raw_token)
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == token_hash)
    ).scalar_one_or_none()

    if not session:
        return None

    # Check expiry
    if session.expires_at < datetime.now(timezone.utc):
        return None

    # Check revocation
    if session.revoked_at is not None:
        return None

    return session

def update_session_last_seen(db: Session, session: UserSession) -> None:
    """Explicitly update the last_seen_at timestamp. Leaves commit to caller."""
    session.last_seen_at = datetime.now(timezone.utc)
    db.add(session)
    db.flush()

def revoke_session(db: Session, raw_token: str) -> bool:
    """Revoke a session by raw token. Leaves commit to caller."""
    token_hash = hash_session_token(raw_token)
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == token_hash)
    ).scalar_one_or_none()

    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.add(session)
        db.flush()
        return True

    return False
