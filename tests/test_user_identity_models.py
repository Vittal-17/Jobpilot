import pytest
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone, timedelta

from app.db.models import User, UserSession, UserProfile
from sqlalchemy import select

def test_user_creation_and_email_uniqueness(db_session):
    u1 = User(
        email="test1@example.com",
        password_hash="hash1",
        display_name="Test User 1"
    )
    db_session.add(u1)
    db_session.commit()

    assert u1.id is not None
    assert u1.email == "test1@example.com"

    # Test case-insensitive uniqueness
    u2 = User(
        email="TEST1@EXAMPLE.com",
        password_hash="hash2",
        display_name="Test User 2"
    )
    db_session.add(u2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_user_email_format_constraint(db_session):
    u = User(
        email="invalid_email",
        password_hash="hash",
    )
    db_session.add(u)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_user_email_empty_constraint(db_session):
    u = User(
        email="   ",
        password_hash="hash",
    )
    db_session.add(u)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_user_password_empty_constraint(db_session):
    u = User(
        email="test@example.com",
        password_hash="   ",
    )
    db_session.add(u)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_user_session_fk_and_cascade(db_session):
    u = User(email="session_user@example.com", password_hash="hash")
    db_session.add(u)
    db_session.commit()

    session1 = UserSession(
        user_id=u.id,
        token_hash="token1",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    db_session.add(session1)
    db_session.commit()

    assert session1.id is not None
    assert session1.user.id == u.id

    # Delete user, session should be deleted (cascade)
    db_session.delete(u)
    db_session.commit()

    session_db = db_session.execute(select(UserSession).where(UserSession.id == session1.id)).scalar_one_or_none()
    assert session_db is None

def test_user_profile_one_to_one(db_session):
    u = User(email="profile_user@example.com", password_hash="hash")
    db_session.add(u)
    db_session.commit()

    p1 = UserProfile(
        user_id=u.id,
        headline="Software Engineer",
        experience_years=5
    )
    db_session.add(p1)
    db_session.commit()

    # Try adding a second profile for the same user
    p2 = UserProfile(
        user_id=u.id,
        headline="Another Engineer",
        experience_years=3
    )
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_user_profile_negative_experience_constraint(db_session):
    u = User(email="profile2_user@example.com", password_hash="hash")
    db_session.add(u)
    db_session.commit()

    p = UserProfile(
        user_id=u.id,
        experience_years=-1
    )
    db_session.add(p)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
