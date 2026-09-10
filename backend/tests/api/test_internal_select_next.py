import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings

client = TestClient(app)

def test_select_next_unauthorized():
    response = client.post("/ingestion/internal/select-next", headers={})
    assert response.status_code == 401

@patch("app.services.provider_router.route_provider")
@patch("app.services.search_selector.select_next_search")
def test_select_next_success(mock_select, mock_route):
    from app.providers.types import ProviderName
    from app.services.provider_router import ProviderSelectionResult
    from app.db.database import get_db

    db = MagicMock()
    db.execute.return_value.scalar_one.return_value = 1
    app.dependency_overrides[get_db] = lambda: db
    mock_result = MagicMock()
    mock_result.candidate.role_id = "ROLE-1"
    mock_result.candidate.role_canonical = "Dev"
    mock_result.candidate.location_id = "LOC-1"
    mock_result.candidate.location_canonical = "City"
    mock_result.candidate.priority = 1
    mock_result.candidate.candidate_id = "CID-1"
    mock_result.reason = "highest_ranked_eligible"
    mock_result.score = 50
    mock_result.execution_id = 99
    mock_result.policy_version = "v1"

    mock_select.return_value = mock_result
    mock_route.return_value = ProviderSelectionResult(
        provider=ProviderName.ADZUNA,
        reason="provider_priority_then_remaining_capacity",
    )

    try:
        response = client.post(
            "/ingestion/internal/select-next",
            headers={"x-api-key": settings.api_secret_key}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["candidate_id"] == "CID-1"
    assert data["execution_id"] == 99
    assert data["intent"]["execution_id"] == 99
    assert data["reason"] == "highest_ranked_eligible"
    assert data["intent"]["role_id"] == "ROLE-1"
    assert data["intent"]["location_id"] == "LOC-1"
    assert data["provider"] == "adzuna"
    assert data["intent"]["provider"] == "adzuna"
    assert data["provider_policy_version"] == "v1"

@patch("app.services.search_selector.select_next_search")
def test_select_next_no_candidate(mock_select):
    mock_result = MagicMock()
    mock_result.candidate = None
    mock_result.reason = "all_candidates_ineligible_or_fresh"
    mock_result.score = None
    mock_result.policy_version = "v1"

    mock_select.return_value = mock_result

    response = client.post(
        "/ingestion/internal/select-next",
        headers={"x-api-key": settings.api_secret_key}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["candidate_id"] is None
    assert data["intent"] is None
    assert data["reason"] == "all_candidates_ineligible_or_fresh"


@pytest.mark.parametrize(
    ("routing_error", "expected_status", "expected_detail"),
    [
        ("quota", 429, "All providers are quota exhausted"),
        ("configuration", 503, "No provider is available"),
        ("database", 503, "Provider routing temporarily unavailable"),
    ],
)
@patch("app.services.provider_router.route_provider")
@patch("app.services.search_selector.select_next_search")
def test_select_next_routing_failures_are_explicit_and_close_claim(
    mock_select,
    mock_route,
    routing_error,
    expected_status,
    expected_detail,
):
    from app.db.database import get_db
    from app.services.provider_router import (
        ProviderNotConfigured,
        ProviderQuotaExhausted,
        ProviderRoutingUnavailable,
    )

    errors = {
        "quota": ProviderQuotaExhausted("exhausted"),
        "configuration": ProviderNotConfigured("missing"),
        "database": ProviderRoutingUnavailable("db"),
    }
    result = MagicMock()
    result.candidate = MagicMock()
    result.execution_id = 99
    mock_select.return_value = result
    mock_route.side_effect = errors[routing_error]
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = client.post(
            "/ingestion/internal/select-next",
            headers={"x-api-key": settings.api_secret_key},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert db.commit.called
    update_sql = " ".join(str(db.execute.call_args.args[0]).split())
    assert "status = 'failed'" in update_sql
    assert "WHERE id = :execution_id AND status = 'selected'" in update_sql


@patch("app.services.provider_router.route_provider")
@patch("app.services.search_selector.select_next_search")
def test_routing_db_failure_does_not_leak_cleanup_failure(mock_select, mock_route):
    from app.db.database import get_db
    from app.services.provider_router import ProviderRoutingUnavailable

    result = MagicMock()
    result.candidate = MagicMock()
    result.execution_id = 99
    mock_select.return_value = result
    mock_route.side_effect = ProviderRoutingUnavailable("secret database detail")
    db = MagicMock()
    db.execute.side_effect = RuntimeError("cleanup database detail")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = client.post(
            "/ingestion/internal/select-next",
            headers={"x-api-key": settings.api_secret_key},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Provider routing temporarily unavailable"}
