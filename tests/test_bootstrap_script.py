import os
import sys
import pytest
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

from app.db.models.user import User
from app.db.models.user_profile import UserProfile
from app.db.models.user_search import UserSearch
from app.core.security import verify_password
from bootstrap import bootstrap_single_user

def test_missing_password_fails(db_session: Session):
    with pytest.raises(ValueError, match="Bootstrap password must be provided"):
        bootstrap_single_user(db_session, email="admin@jobpilot.local", password="")

def test_supplied_password_creates_user_1(db_session: Session):
    bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")
    user = db_session.query(User).filter(User.id == 1).first()
    assert user is not None
    assert user.email == "admin@jobpilot.local"
    assert user.password_hash != "SecurePassword123"
    assert verify_password("SecurePassword123", user.password_hash)

def test_repeated_bootstrap_is_idempotent(db_session: Session):
    bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")
    bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")
    assert db_session.query(User).count() == 1
    assert db_session.query(UserProfile).count() == 1
    assert db_session.query(UserSearch).count() == 1

def test_conflicting_email_configuration_rejected(db_session: Session):
    bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")
    with pytest.raises(ValueError, match="Cannot bootstrap over existing identity"):
        bootstrap_single_user(db_session, email="other@jobpilot.local", password="SecurePassword123")

def test_email_used_by_different_user_rejected(db_session: Session):
    user2 = User(id=2, email="admin@jobpilot.local", password_hash="hash")
    db_session.add(user2)
    db_session.commit()
    with pytest.raises(ValueError, match="is already in use by User ID 2"):
        bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")

def test_bootstrap_does_not_desync_sequence(db_session: Session):
    bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")
    db_session.commit()
    u2 = User(email="test2@jobpilot.local", password_hash="123")
    db_session.add(u2)
    db_session.commit()
    assert u2.id > 1

def test_sequence_sync_failure_aborts_transaction(db_session: Session, monkeypatch):
    import sqlalchemy
    original_text = sqlalchemy.text

    def mock_text(query):
        if "setval" in query.lower():
            return original_text("SELECT THIS_IS_INVALID_SQL_SYNTAX")
        return original_text(query)

    monkeypatch.setattr("bootstrap.text", mock_text)

    import sqlalchemy.exc
    with pytest.raises(sqlalchemy.exc.ProgrammingError):
        bootstrap_single_user(db_session, email="admin@jobpilot.local", password="SecurePassword123")

    db_session.rollback()
    assert db_session.query(User).count() == 0
