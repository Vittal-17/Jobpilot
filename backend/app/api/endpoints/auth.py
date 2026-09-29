from typing import Optional
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status, Cookie
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.database import get_db
from app.schemas.auth import LoginRequest, UserCreate, UserResponse
from app.services import auth_service
from app.core.security import SESSION_COOKIE_NAME

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


import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Response, status, Cookie, Request

# Simple in-memory rate limiter for login
LOGIN_ATTEMPTS = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 10
LOGIN_BLOCK_TIME_SECONDS = 60

def is_login_rate_limited(identifier: str) -> bool:
    now = time.time()
    valid_attempts = [t for t in LOGIN_ATTEMPTS[identifier] if now - t < LOGIN_BLOCK_TIME_SECONDS]

    if not valid_attempts:
        if identifier in LOGIN_ATTEMPTS:
            del LOGIN_ATTEMPTS[identifier]
    else:
        LOGIN_ATTEMPTS[identifier] = valid_attempts

    # Python dictionaries are safe to query for missing keys,
    # but since we might have deleted it, we check using get()
    if len(LOGIN_ATTEMPTS.get(identifier, [])) >= MAX_LOGIN_ATTEMPTS:
        return True

    LOGIN_ATTEMPTS[identifier].append(now)
    return False

def get_real_ip(request: Request) -> str:
    xfwd = request.headers.get("x-forwarded-for")
    if xfwd:
        ips = [ip.strip() for ip in xfwd.split(",")]
        if ips:
            return ips[-1]
    return request.client.host if request.client else "unknown"

@router.post(
    "/login",
    response_model=UserResponse,
    summary="Log in a user",
    description="Authenticates a user and sets a secure HttpOnly session cookie."
)
def login(
    login_req: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db)
):
    client_ip = get_real_ip(request)

    # Rate limit by IP
    if is_login_rate_limited(f"ip:{client_ip}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later."
        )

    # Rate limit by Email
    if is_login_rate_limited(f"email:{login_req.email.lower()}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts for this account. Please try again later."
        )

    try:
        user = auth_service.authenticate_user(db, login_req.email, login_req.password)
        if not user:
            # Generic error
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )

        # Update last login
        user.last_login_at = datetime.now(timezone.utc)

        # Create session
        session, raw_token = auth_service.create_session(db, user.id)
        db.commit()

        # Set cookie
        max_age = auth_service.SESSION_EXPIRY_DAYS * 24 * 60 * 60
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=raw_token,
            max_age=max_age,
            expires=session.expires_at,
            httponly=True,
            secure=True,
            samesite="lax",
        )

        return user
    except HTTPException:
        # Re-raise HTTP exceptions (like 401)
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error during login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out a user",
    description="Revokes the active session and clears the session cookie."
)
def logout(
    response: Response,
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db)
):
    if session_token:
        try:
            revoked = auth_service.revoke_session(db, session_token)
            if revoked:
                db.commit()
            else:
                db.rollback()
        except Exception as e:
            db.rollback()
            logger.error(f"Error during logout: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )

    # Always clear the cookie regardless of server-side session state
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite="lax",
    )
