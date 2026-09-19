from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_db
from app.db.models.search_execution import SearchExecutionModel
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.main import app

client = TestClient(app)


def _setup_mocked_gateway(monkeypatch, db_session, candidate_id: str, query_variant: str | None = None):
    """Sets up the DB claim and overrides the dependencies for the gateway test."""
    app.dependency_overrides[get_db] = lambda: db_session

    claim = SearchExecutionModel(
        candidate_id=candidate_id,
        status="selected",
        provider_name="adzuna",
        query_variant=query_variant,
        selected_at=datetime.now(timezone.utc),
    )
    db_session.add(claim)
    db_session.commit()

    # Mock run_ingestion so we don't actually hit the external provider
    mock_run = MagicMock()
    mock_run.return_value = (MagicMock(provider="adzuna", failed=0), [])
    monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run)

    return claim


def test_gateway_canonical_keywords_pass(db_session, monkeypatch):
    claim = _setup_mocked_gateway(monkeypatch, db_session, "ROLE-DA-002::LOC-BLR-001")
    try:
        intent = {
            "role_id": "ROLE-DA-002",
            "keywords": "Data Engineer",
            "location_id": "LOC-BLR-001",
            "location": "Bengaluru",
            "priority": 1,
            "execution_id": claim.id,
            "provider": "adzuna",
        }
        res = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json=intent,
        )
        assert res.status_code != 409
    finally:
        app.dependency_overrides.clear()


def test_gateway_valid_fresher_variant_passes(db_session, monkeypatch):
    claim = _setup_mocked_gateway(monkeypatch, db_session, "ROLE-DA-002::LOC-BLR-001", query_variant="Junior Data Engineer")
    try:
        intent = {
            "role_id": "ROLE-DA-002",
            "keywords": "Junior Data Engineer",
            "location_id": "LOC-BLR-001",
            "location": "Bengaluru",
            "priority": 1,
            "execution_id": claim.id,
            "provider": "adzuna",
        }
        res = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json=intent,
        )
        assert res.status_code != 409
    finally:
        app.dependency_overrides.clear()


def test_gateway_unrelated_keyword_fails(db_session, monkeypatch):
    claim = _setup_mocked_gateway(monkeypatch, db_session, "ROLE-DA-002::LOC-BLR-001", query_variant="Data Engineer")
    try:
        intent = {
            "role_id": "ROLE-DA-002",
            "keywords": "Gardener",
            "location_id": "LOC-BLR-001",
            "location": "Bengaluru",
            "priority": 1,
            "execution_id": claim.id,
            "provider": "adzuna",
        }
        res = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json=intent,
        )
        assert res.status_code == 409
        assert "Execution keywords do not match claimed variant" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_gateway_foreign_variant_fails(db_session, monkeypatch):
    claim = _setup_mocked_gateway(monkeypatch, db_session, "ROLE-DA-002::LOC-BLR-001", query_variant="Data Engineer")
    try:
        intent = {
            "role_id": "ROLE-DA-002",
            "keywords": "Junior Backend Developer",
            "location_id": "LOC-BLR-001",
            "location": "Bengaluru",
            "priority": 1,
            "execution_id": claim.id,
            "provider": "adzuna",
        }
        res = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json=intent,
        )
        assert res.status_code == 409
        assert "Execution keywords do not match claimed variant" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_gateway_user_search_fallback_passes(db_session, monkeypatch):
    claim = _setup_mocked_gateway(monkeypatch, db_session, "user_search::999")
    try:
        # User Search specifics
        db_session.add(User(id=1, email="test@test.com", password_hash="hash"))
        db_session.add(
            UserSearch(
                id=999,
                user_id=1,
                query="Machine Learning",
                location="Remote",
                enabled=True,
            )
        )
        db_session.commit()

        intent = {
            "role_id": "user_search",
            "keywords": "Machine Learning",
            "location_id": "user_search",
            "location": "Remote",
            "priority": 3,
            "execution_id": claim.id,
            "provider": "adzuna",
        }
        res = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json=intent,
        )
        assert res.status_code != 409
    finally:
        app.dependency_overrides.clear()
