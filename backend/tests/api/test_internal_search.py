import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings
from app.schemas.job_search import IngestionResult

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
def test_internal_search_success(mock_run_ingestion):
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
