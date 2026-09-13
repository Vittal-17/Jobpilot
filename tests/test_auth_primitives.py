import pytest
from datetime import datetime, timezone, timedelta
from app.core.security import get_password_hash, verify_password, generate_session_token, hash_session_token
from app.schemas.auth import UserCreate
from app.services.auth_service import (
    create_user,
    authenticate_user,
    create_session,
    get_valid_session,
    update_session_last_seen,
    revoke_session,
    get_normalized_email
)

def test_password_hashing():
    password = "supersecretpassword"
    hashed = get_password_hash(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrongpassword", hashed)

def test_password_hashing_oversized():
    # Passwords over 72 bytes should be rejected
    oversized_password = "a" * 73
    with pytest.raises(ValueError, match="exceeds maximum supported length"):
        get_password_hash(oversized_password)

    # verify_password should return False for oversized passwords instead of crashing
    hashed = get_password_hash("normal_password")
    assert not verify_password(oversized_password, hashed)

def test_session_token_hashing():
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)

    assert raw_token != token_hash
    assert len(raw_token) > 20
    assert hash_session_token(raw_token) == token_hash

def test_email_normalization():
    assert get_normalized_email("  Test@EXAMPLE.com  ") == "test@example.com"

def test_user_creation_and_authentication(db_session):
    user_create = UserCreate(email="TestUser@Example.com", password="SecurePassword123!", display_name="Test User")
    user = create_user(db_session, user_create)
    db_session.commit()

    assert user.id is not None
    assert user.email == "testuser@example.com"

    # Successful auth
    auth_user = authenticate_user(db_session, "testuser@EXAMPLE.com", "SecurePassword123!")
    assert auth_user is not None
    assert auth_user.id == user.id

    # Failed auth - wrong password
    assert authenticate_user(db_session, "testuser@example.com", "wrong") is None

    # Failed auth - non-existent email (timing path check implicit by not crashing)
    assert authenticate_user(db_session, "nonexistent@example.com", "SecurePassword123!") is None

    # Inactive user auth
    user.is_active = False
    db_session.commit()
    assert authenticate_user(db_session, "testuser@example.com", "SecurePassword123!") is None

def test_session_management(db_session):
    user_create = UserCreate(email="sessionuser@example.com", password="password")
    user = create_user(db_session, user_create)
    db_session.commit()

    # Create session
    session, raw_token = create_session(db_session, user.id)
    db_session.commit()
    assert session.id is not None
    assert session.user_id == user.id
    assert session.token_hash != raw_token

    # Get valid session
    valid_session = get_valid_session(db_session, raw_token)
    assert valid_session is not None
    assert valid_session.id == session.id

    # Update last seen
    assert valid_session.last_seen_at is None
    update_session_last_seen(db_session, valid_session)
    db_session.commit()
    assert valid_session.last_seen_at is not None

    # Revoke session
    assert revoke_session(db_session, raw_token) is True
    db_session.commit()
    assert revoke_session(db_session, raw_token) is False # Already revoked

    # Get revoked session
    revoked_session = get_valid_session(db_session, raw_token)
    assert revoked_session is None

def test_expired_session(db_session):
    user_create = UserCreate(email="expireuser@example.com", password="password")
    user = create_user(db_session, user_create)
    db_session.commit()

    session, raw_token = create_session(db_session, user.id)

    # Manually expire the session
    session.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    assert get_valid_session(db_session, raw_token) is None

def test_service_transaction_rollback(db_session):
    """Prove service primitives do not commit implicitly by verifying rollback drops changes."""
    user_create = UserCreate(email="rollback@example.com", password="password")

    # Test create_user rollback
    user = create_user(db_session, user_create)
    assert user.id is not None
    db_session.rollback()
    assert authenticate_user(db_session, "rollback@example.com", "password") is None

    # Set up user for session tests
    user = create_user(db_session, user_create)
    db_session.commit()

    # Test create_session rollback
    session, raw_token = create_session(db_session, user.id)
    assert session.id is not None
    db_session.rollback()
    assert get_valid_session(db_session, raw_token) is None

    # Set up session for further tests
    session, raw_token = create_session(db_session, user.id)
    db_session.commit()

    # Test update_session_last_seen rollback
    valid_session = get_valid_session(db_session, raw_token)
    assert valid_session.last_seen_at is None
    update_session_last_seen(db_session, valid_session)
    db_session.rollback()

    # Need a fresh lookup after rollback because the instance expires
    valid_session = get_valid_session(db_session, raw_token)
    assert valid_session.last_seen_at is None

    # Test revoke_session rollback
    assert revoke_session(db_session, raw_token) is True
    db_session.rollback()
    assert get_valid_session(db_session, raw_token) is not None
