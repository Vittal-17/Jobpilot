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

def test_internal_search_rejects_provider_without_execution():
    headers = {"X-Api-Key": settings.api_secret_key}
    response = client.post("/ingestion/internal/search", headers=headers, json={
        "role_id": "ROLE-PY-001",
        "keywords": "Python",
        "location_id": "LOC-BLR-001",
        "location": "Bengaluru",
        "provider": "adzuna"
    })
    assert response.status_code == 422

@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_success_without_execution_id_remains_legacy_compatible(mock_run_ingestion):
    # Mocking provider calls ensures no real quota is consumed during tests
    mock_run_ingestion.return_value = (IngestionResult(
        provider="adzuna",
        fetched=10,
        created=5,
        duplicates=5,
        invalid=0,
        failed=0
    ), [])
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
    claim = MagicMock(id=99, candidate_id="ROLE-PY-001::LOC-BLR-001", status="selected", provider_name="adzuna")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db
    mock_run_ingestion.return_value = (IngestionResult(provider="adzuna"), [])
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
                "provider": "adzuna",
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
        (MagicMock(candidate_id="ROLE-PY-001::LOC-BLR-001", status="started", provider_name="adzuna"), 99, 409),
        (MagicMock(candidate_id="ROLE-PY-002::LOC-BLR-001", status="selected", provider_name="adzuna"), 99, 409),
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
                "provider": "adzuna",
            },
        )
        assert response.status_code == expected_status
    finally:
        app.dependency_overrides.clear()


def test_internal_search_rejects_spoofed_canonical_keywords():
    claim = MagicMock(candidate_id="ROLE-PY-001::LOC-BLR-001", status="selected", provider_name="adzuna")
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
                "provider": "adzuna",
            },
        )
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()


@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_rejects_provider_remap(mock_run_ingestion):
    claim = MagicMock(
        candidate_id="ROLE-PY-001::LOC-BLR-001",
        status="selected",
        provider_name="adzuna",
    )
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
                "execution_id": 99,
                "provider": "jooble",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Execution provider does not match routing decision"
        mock_run_ingestion.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_internal_search_rejects_unknown_provider():
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
            "provider": "importlib.evil",
        },
    )
    assert response.status_code == 422

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


@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_end_to_end_adaptive_retrieval_location(mock_run_ingestion):
    """
    Proves that when /select-next emits a broadened retrieval_location (e.g. 'Bengaluru' for LOC-BLR-002),
    /internal/search accepts that exact retrieval_location, derives it from the claim, and passes it to provider.
    """
    claim = MagicMock(
        id=101,
        candidate_id="ROLE-PY-001::LOC-BLR-002",
        status="selected",
        provider_name="adzuna",
        query_variant="Junior Python Developer",
        retrieval_location="Bengaluru",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db
    mock_run_ingestion.return_value = (IngestionResult(provider="adzuna"), [])

    try:
        response = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json={
                "role_id": "ROLE-PY-001",
                "keywords": "Junior Python Developer",
                "location_id": "LOC-BLR-002",
                "location": "Bengaluru",
                "priority": 1,
                "execution_id": 101,
                "provider": "adzuna",
            },
        )
        assert response.status_code == 200
        mock_run_ingestion.assert_called_once()
        passed_query = mock_run_ingestion.call_args.args[3]
        assert passed_query.location == "Bengaluru"
        assert passed_query.keywords == "Junior Python Developer"
    finally:
        app.dependency_overrides.clear()


@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_rejects_different_variant_than_claimed(mock_run_ingestion):
    """
    Proves that an execution claimed as Variant A ('Junior Python Developer') strictly rejects
    Variant B ('Python Developer') even when Variant B is a valid bounded variant for the candidate.
    Guarantees telemetry cannot be contaminated across variants.
    """
    claim = MagicMock(
        id=102,
        candidate_id="ROLE-PY-001::LOC-BLR-001",
        status="selected",
        provider_name="adzuna",
        query_variant="Junior Python Developer",
        retrieval_location=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db

    try:
        response = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json={
                "role_id": "ROLE-PY-001",
                "keywords": "Python Developer",  # Valid bounded variant, but not the claimed variant!
                "location_id": "LOC-BLR-001",
                "location": "Bengaluru",
                "priority": 1,
                "execution_id": 102,
                "provider": "adzuna",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Execution keywords do not match claimed variant"
        mock_run_ingestion.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@patch("app.api.endpoints.ingestion.run_ingestion")
def test_internal_search_supports_legacy_null_query_variant(mock_run_ingestion):
    """
    Proves backwards compatibility for legacy execution claims where query_variant is NULL:
    accepts candidate canonical role without weakening exact variant validation for new claims.
    """
    claim = MagicMock(
        id=103,
        candidate_id="ROLE-PY-001::LOC-BLR-001",
        status="selected",
        provider_name="adzuna",
        query_variant=None,  # Legacy claim with NULL query_variant
        retrieval_location=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = claim
    app.dependency_overrides[get_db] = lambda: db
    mock_run_ingestion.return_value = (IngestionResult(provider="adzuna"), [])

    try:
        response = client.post(
            "/ingestion/internal/search",
            headers={"X-Api-Key": settings.api_secret_key},
            json={
                "role_id": "ROLE-PY-001",
                "keywords": "Python Developer",  # Candidate canonical role
                "location_id": "LOC-BLR-001",
                "location": "Bengaluru",
                "priority": 1,
                "execution_id": 103,
                "provider": "adzuna",
            },
        )
        assert response.status_code == 200
        mock_run_ingestion.assert_called_once()
        passed_query = mock_run_ingestion.call_args.args[3]
        assert passed_query.keywords == "Python Developer"
    finally:
        app.dependency_overrides.clear()
