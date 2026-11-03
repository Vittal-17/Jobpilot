"""Regression coverage for recommendation ownership and anonymous access."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.database import get_db
from app.db.models.job import JobModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User
from app.main import app


def test_anonymous_recommendations_are_rejected():
    """Recommendation history must never be an unauthenticated listing."""
    app.dependency_overrides.clear()
    try:
        response = TestClient(app).get("/v1/recommendations")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_recommendations_only_return_the_authenticated_users_history(db_session):
    owner = User(email="recommendation-owner@example.com", password_hash="pw", is_active=True)
    other = User(email="recommendation-other@example.com", password_hash="pw", is_active=True)
    db_session.add_all([owner, other])
    db_session.flush()

    owner_job = JobModel(
        title="Owner job",
        company="Owner Co",
        source="privacy-test",
        source_job_id="owner-recommendation-job",
        discovered_at=datetime.now(timezone.utc),
    )
    other_job = JobModel(
        title="Other job",
        company="Other Co",
        source="privacy-test",
        source_job_id="other-recommendation-job",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add_all([owner_job, other_job])
    db_session.flush()
    db_session.add_all([
        RecommendationHistoryModel(user_id=owner.id, job_id=owner_job.id),
        RecommendationHistoryModel(user_id=other.id, job_id=other_job.id),
    ])
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        response = TestClient(app).get("/v1/recommendations")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["job"]["id"] for item in payload["items"]] == [owner_job.id]
    assert payload["items"][0]["job"]["title"] == "Owner job"
