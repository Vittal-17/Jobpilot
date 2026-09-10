import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings

client = TestClient(app)

def test_select_next_unauthorized():
    response = client.post("/ingestion/internal/select-next", headers={})
    assert response.status_code == 401

@patch("app.services.search_selector.select_next_search")
def test_select_next_success(mock_select):
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

    response = client.post(
        "/ingestion/internal/select-next",
        headers={"x-api-key": settings.api_secret_key}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["candidate_id"] == "CID-1"
    assert data["execution_id"] == 99
    assert data["intent"]["execution_id"] == 99
    assert data["reason"] == "highest_ranked_eligible"
    assert data["intent"]["role_id"] == "ROLE-1"
    assert data["intent"]["location_id"] == "LOC-1"

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
