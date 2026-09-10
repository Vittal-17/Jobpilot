import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings
from app.schemas.job_search import IngestionResult
from app.db.database import get_db

client = TestClient(app)

def test_internal_search_missing_auth():
    response = client.post("/ingestion/internal/search", json={
        "role_id": "ROLE-PY-001",
        "keywords": "Python",
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru"
    })
    assert response.status_code == 401

def test_internal_search_invalid_auth():
    response = client.post("/ingestion/internal/search", headers={"X-Api-Key": "wrong"}, json={
        "role_id": "ROLE-PY-001",
        "keywords": "Python",
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru"
    })
    assert response.status_code == 401

def test_internal_search_malformed_request():
    headers = {"X-Api-Key": settings.api_secret_key}
    response = client.post("/ingestion/internal/search", headers=headers, json={
        "role_id": "ROLE-PY-001",
        # missing keywords
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru"
    })
    assert response.status_code == 422

def test_internal_search_unsupported_provider_field():
    headers = {"X-Api-Key": settings.api_secret_key}
    response = client.post("/ingestion/internal/search", headers=headers, json={
        "role_id": "ROLE-PY-001",
        "keywords": "Python",
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru",
        "provider": "adzuna"  # Extra fields forbidden
    })
    assert response.status_code == 422

@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_success_without_execution_id_remains_legacy_compatible(mock_run_ingestion):
    # Mocking provider calls ensures no real quota is consumed during tests
    mock_run_ingestion.return_value = IngestionResult(
        provider="adzuna",
        fetched=10,
        created=5,
        duplicates=5,
        invalid=0,
        failed=0
    )
    headers = {"X-Api-Key": settings.api_secret_key}
    response = client.post("/ingestion/internal/search", headers=headers, json={
        "role_id": "ROLE-PY-001",
        "keywords": "Python",
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru",
        "priority": 1
    })
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "adzuna"
    assert data["fetched"] == 10
    assert data["created"] == 5
    mock_run_ingestion.assert_called_once()
    # Confirm the selected provider was Adzuna
    args, kwargs = mock_run_ingestion.call_args
    assert args[1] == "adzuna"


@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_matching_execution_is_accepted(mock_run_ingestion):
    claim = MagicMock(id=99, candidate_id="ROLE-PY-001::LOC-BLR-001", status="selected")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db
    mock_run_ingestion.return_value = IngestionResult(provider="adzuna")
    try:
        response = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json={
                "role_id": "ROLE-PY-001",
                "keywords": "Python Developer",
                "location_id": "LOC-BLR-001",
                "location": "Bengaluru",
                "priority": 1,
                "execution_id": 99,
            },
        )
        assert response.status_code == 200
        assert mock_run_ingestion.call_args.args[4] == 99
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("claim", "execution_id", "expected_status"),
    [
        (None, 999, 404),
        (MagicMock(candidate_id="ROLE-PY-001::LOC-BLR-001", status="started"), 99, 409),
        (MagicMock(candidate_id="ROLE-PY-002::LOC-BLR-001", status="selected"), 99, 409),
    ],
)
def test_internal_search_rejects_invalid_execution_correlation(claim, execution_id, expected_status):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json={
                "role_id": "ROLE-PY-001",
                "keywords": "Python Developer",
                "location_id": "LOC-BLR-001",
                "location": "Bengaluru",
                "priority": 1,
                "execution_id": execution_id,
            },
        )
        assert response.status_code == expected_status
    finally:
        app.dependency_overrides.clear()


def test_internal_search_rejects_spoofed_canonical_keywords():
    claim = MagicMock(candidate_id="ROLE-PY-001::LOC-BLR-001", status="selected")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json={
                "role_id": "ROLE-PY-001",
                "keywords": "Unrelated Search",
                "location_id": "LOC-BLR-001",
                "location": "Bengaluru",
                "priority": 1,
                "execution_id": 99,
            },
        )
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()

@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_quota_exceeded(mock_run_ingestion):
    from app.services.ingestion import RateLimitExceeded
    mock_run_ingestion.side_effect = RateLimitExceeded("Limit exceeded for adzuna")
    headers = {"X-Api-Key": settings.api_secret_key}
    response = client.post("/ingestion/internal/search", headers=headers, json={
        "role_id": "ROLE-PY-001",
        "keywords": "Python",
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru"
    })
    assert response.status_code == 429
    assert response.json()["detail"] == "Provider request quota exceeded"
