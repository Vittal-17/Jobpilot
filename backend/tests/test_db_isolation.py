import pytest
from app.db.database import SessionLocal, engine
from app.core.config import settings

def test_sessionlocal_targets_test_database_not_production():
    """Prove that any module importing SessionLocal or engine connects to jobpilot_test, not jobpilot."""
    db_name = engine.url.database
    assert db_name == "jobpilot_test", f"Engine database is '{db_name}', expected 'jobpilot_test'"
    assert db_name != "jobpilot", "Engine must not target production jobpilot database!"

    session_bind_db = SessionLocal.kw["bind"].url.database
    assert session_bind_db == "jobpilot_test", f"SessionLocal bound database is '{session_bind_db}', expected 'jobpilot_test'"
    assert session_bind_db != "jobpilot", "SessionLocal must not target production jobpilot database!"

    with SessionLocal() as session:
        active_db = session.get_bind().url.database
        assert active_db == "jobpilot_test"
        assert active_db != "jobpilot"
