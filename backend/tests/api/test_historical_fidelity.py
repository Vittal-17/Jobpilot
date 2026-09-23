import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.search_execution import SearchExecutionModel
from app.db.models.user_profile import UserProfile
from app.db.models.user import User
from app.db.models.job import JobModel
from app.api.deps import get_current_user
from app.schemas.job import JobResponse

# Mock DB Session
mock_db = MagicMock()

def override_get_db():
    yield mock_db

def override_get_current_user():
    return User(id=1, email="test@jobpilot.cfd")

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_get_current_user
client = TestClient(app)

def test_legacy_recommendation_row():
    # Setup mock returns
    # rec, job
    job = JobModel(id=1, title="Test", company="C", source="S", discovered_at=datetime.now(timezone.utc), description_is_snippet=False)

    # legacy rec has None for score and reasons
    legacy_rec = RecommendationHistoryModel(user_id=1, job_id=1, score=None, reasons=None, recommended_at=datetime.now(timezone.utc))

    # Mocking the offset().limit().all() chain
    mock_db.execute.return_value.all.return_value = [(legacy_rec, job)]
    mock_db.scalar.return_value = 1

    resp = client.get("/v1/recommendations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["match"] is None


def test_persisted_match_results_survive_preference_change():
    job = JobModel(id=1, title="Test", company="C", source="S", discovered_at=datetime.now(timezone.utc), description_is_snippet=False)

    # A historical row that has score and reasons
    reasons = [{"code": "SKILLS_MATCH", "message": "Matched Python"}]
    rec = RecommendationHistoryModel(user_id=1, job_id=1, score=99, reasons=reasons, recommended_at=datetime.now(timezone.utc))

    mock_db.execute.return_value.all.return_value = [(rec, job)]
    mock_db.scalar.return_value = 1

    resp = client.get("/v1/recommendations")
    assert resp.status_code == 200
    data = resp.json()

    match = data["items"][0]["match"]
    assert match is not None
    assert match["score"] == 99
    assert len(match["reasons"]) == 1
    assert match["reasons"][0]["code"] == "SKILLS_MATCH"
    assert match["reasons"][0]["message"] == "Matched Python"


def test_system_status_zero_executions():
    # Return None for both scalars
    mock_db.scalar.side_effect = [None, None, 0]

    resp = client.get("/v1/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine_active"] is False
    assert data["last_sync"] is None
    assert data["latest_execution_status"] is None


def test_system_status_latest_execution_logic():
    now = datetime.now(timezone.utc)

    # last_sync is completed_at of the latest 'succeeded' run
    last_sync_dt = now - timedelta(hours=1)

    # Returns for the three scalars: last_sync (datetime), latest_status (str), total_processed (int)
    mock_db.scalar.side_effect = [last_sync_dt, "failed", 100]

    resp = client.get("/v1/system/status")
    data = resp.json()

    # Engine is active because there are executions (latest_status is not None)
    assert data["engine_active"] is True

    # Should report failed since the latest run failed
    assert data["latest_execution_status"] == "failed"

    # Should still report the last success time
    assert data["last_sync"] is not None
    assert data["total_processed"] == 100
