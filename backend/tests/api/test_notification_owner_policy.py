"""Focused single-owner notification and semantic acknowledgement tests."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.endpoints.ingestion import (
    NotificationClaimRequest,
    claim_notifications,
    NotificationFailRequest,
    fail_notifications,
)
from app.core.config import settings
from app.db.models.job import JobModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User


def test_claim_payload_is_scoped_to_configured_owner(db_session, monkeypatch):
    owner = User(email="owner-policy@example.com", password_hash="pw", is_active=True)
    other = User(email="other-policy@example.com", password_hash="pw", is_active=False)
    db_session.add_all([owner, other])
    db_session.flush()
    monkeypatch.setenv("TELEGRAM_OWNER_USER_ID", str(owner.id))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "owner-chat")

    owner_job = JobModel(
        title="Owner job", company="Owner Co", source="test", source_job_id="owner-job",
        discovered_at=datetime.now(timezone.utc),
    )
    other_job = JobModel(
        title="Other job", company="Other Co", source="test", source_job_id="other-job",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add_all([owner_job, other_job])
    db_session.flush()
    db_session.add_all([
        RecommendationHistoryModel(user_id=owner.id, job_id=owner_job.id),
        RecommendationHistoryModel(user_id=other.id, job_id=other_job.id),
    ])
    db_session.commit()

    response = claim_notifications(NotificationClaimRequest(delivery_id="owner-delivery", limit=5), db_session)
    assert response.user_id == owner.id
    assert response.telegram_chat_id == "owner-chat"
    assert [job.job_id for job in response.recommendations] == [owner_job.id]

    with pytest.raises(HTTPException) as exc:
        claim_notifications(
            NotificationClaimRequest(user_id=other.id, delivery_id="other-delivery", limit=5), db_session
        )
    assert exc.value.status_code == 409

    # An existing delivery belonging to a different (inactive) user must not
    # become visible merely because the retry omits user_id.
    db_session.execute(
        text("INSERT INTO notification_deliveries (delivery_id, user_id, claimed_at) VALUES ('foreign-delivery', :uid, CURRENT_TIMESTAMP)"),
        {"uid": other.id},
    )
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        claim_notifications(NotificationClaimRequest(delivery_id="foreign-delivery", limit=5), db_session)
    assert exc.value.status_code == 409


def test_definitive_failure_requires_send_start(db_session, monkeypatch):
    owner = User(email="failure-owner@example.com", password_hash="pw", is_active=True)
    db_session.add(owner)
    db_session.commit()
    monkeypatch.setenv("TELEGRAM_OWNER_USER_ID", str(owner.id))

    job = JobModel(
        title="Owner job", company="Owner Co", source="test", source_job_id="failure-job",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(RecommendationHistoryModel(user_id=owner.id, job_id=job.id))
    db_session.commit()
    claim_notifications(NotificationClaimRequest(delivery_id="failure-before-start", limit=1), db_session)

    with pytest.raises(HTTPException) as exc:
        fail_notifications(
            NotificationFailRequest(
                user_id=owner.id,
                delivery_id="failure-before-start",
                reason="Telegram rejected",
                definitive=True,
            ),
            db_session,
        )
    assert exc.value.status_code == 409
    assert "send-start" in exc.value.detail


def test_multiple_active_users_fail_closed_for_notifications(db_session):
    db_session.add_all([
        User(email="active-one@example.com", password_hash="pw", is_active=True),
        User(email="active-two@example.com", password_hash="pw", is_active=True),
    ])
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        claim_notifications(NotificationClaimRequest(delivery_id="ambiguous-owner", limit=1), db_session)
    assert exc.value.status_code == 409
    assert "exactly one" in exc.value.detail.lower()


def test_workflow_ack_payload_requires_telegram_ok_and_message_id():
    workflow = json.loads(Path("backend/JP___Notifications.json").read_text(encoding="utf-8"))[0]
    nodes = {node["name"]: node for node in workflow["nodes"]}
    telegram = nodes["Send Telegram"]
    assert telegram["type"] == "n8n-nodes-base.httpRequest"
    assert telegram["parameters"]["options"]["response"]["response"]["fullResponse"] is True
    assert "TELEGRAM_BOT_TOKEN" in telegram["parameters"]["url"]
    check = nodes["Check Telegram Success"]
    check_expression = check["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert "statusCode >= 200" in check_expression
    assert "statusCode < 300" in check_expression
    ack = nodes["Acknowledge Delivery"]
    expression = ack["parameters"]["jsonBody"]
    assert "body.ok !== true" in expression
    assert "typeof body !== 'object'" in expression
    assert "Array.isArray(body)" in expression
    assert "message_id" in expression
    assert "JSON.stringify" in expression
    # The Telegram node remains no-retry, so malformed/timeout responses leave
    # the send-started claim as the durable unknown fence.
    assert telegram.get("retryOnFail") is not True


def test_firecrawl_wrapper_validation_returns_422():
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/ingestion/internal/firecrawl-discovery/run",
        headers={"X-Api-Key": settings.api_secret_key},
        json={"plan": {"queries": [{"keywords": "missing location"}]}},
    )
    assert response.status_code == 422


def test_firecrawl_wrapper_plan_conflict_returns_409():
    from app.main import app

    client = TestClient(app)
    headers = {"X-Api-Key": settings.api_secret_key}
    first = client.post(
        "/ingestion/internal/firecrawl-discovery/run",
        headers=headers,
        json={"run_id": "api-conflict-run", "force": True, "plan": {"queries": []}},
    )
    assert first.status_code == 200
    second = client.post(
        "/ingestion/internal/firecrawl-discovery/run",
        headers=headers,
        json={
            "run_id": "api-conflict-run",
            "force": True,
            "plan": {
                "queries": [{"keywords": "different", "location": "Bengaluru", "page_size": 10}]
            },
        },
    )
    assert second.status_code == 409
