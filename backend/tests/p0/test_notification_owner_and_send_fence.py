"""P0 database regressions for single-owner notification delivery."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.endpoints.ingestion import (
    NotificationAcknowledgeRequest,
    NotificationClaimRequest,
    NotificationFailRequest,
    NotificationSendStartRequest,
    acknowledge_notifications,
    claim_notifications,
    fail_notifications,
    recover_stale_pre_send_claims,
    start_notification_send,
)
from app.db.models.job import JobModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User


def _owner_and_other(db_session, monkeypatch, *, other_active=False):
    owner = User(email="p0-owner@example.com", password_hash="test", is_active=True)
    other = User(email="p0-other@example.com", password_hash="test", is_active=other_active)
    db_session.add_all([owner, other])
    db_session.flush()
    monkeypatch.setenv("TELEGRAM_OWNER_USER_ID", str(owner.id))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "owner-chat")
    job_ids = []
    for user, suffix in ((owner, "owner"), (other, "other")):
        job = JobModel(
            title=f"{suffix} job",
            company="P0 Co",
            source="p0-notification",
            source_job_id=suffix,
            discovered_at=datetime.now(timezone.utc),
        )
        db_session.add(job)
        db_session.flush()
        job_ids.append(job.id)
        db_session.add(RecommendationHistoryModel(user_id=user.id, job_id=job.id))
    db_session.commit()
    return owner.id, other.id, job_ids


def test_multiple_active_users_fail_closed_even_with_explicit_owner(db_session, monkeypatch):
    owner, _, _ = _owner_and_other(db_session, monkeypatch, other_active=True)
    with pytest.raises(HTTPException) as failure:
        claim_notifications(NotificationClaimRequest(user_id=owner, delivery_id="p0-ambiguous"), db_session)
    assert failure.value.status_code == 409
    assert db_session.execute(text("SELECT COUNT(*) FROM notification_deliveries")).scalar_one() == 0
    assert db_session.execute(
        text("SELECT COUNT(*) FROM recommendation_history WHERE delivery_id IS NOT NULL")
    ).scalar_one() == 0


def test_owner_only_payload_and_cross_user_operations_have_zero_effect(db_session, monkeypatch):
    owner, other, job_ids = _owner_and_other(db_session, monkeypatch)
    response = claim_notifications(NotificationClaimRequest(delivery_id="p0-owner-send"), db_session)
    assert response.user_id == owner
    assert response.telegram_chat_id == "owner-chat"
    assert [job.job_id for job in response.recommendations] == [job_ids[0]]

    operations = [
        (claim_notifications, NotificationClaimRequest(user_id=other, delivery_id="p0-other-send")),
        (start_notification_send, NotificationSendStartRequest(user_id=other, delivery_id="p0-owner-send")),
        (acknowledge_notifications, NotificationAcknowledgeRequest(user_id=other, delivery_id="p0-owner-send")),
        (fail_notifications, NotificationFailRequest(user_id=other, delivery_id="p0-owner-send", reason="false rejection", definitive=True)),
    ]
    for operation, request in operations:
        with pytest.raises(HTTPException) as failure:
            operation(request, db_session)
        assert failure.value.status_code == 409

    assert db_session.execute(
        text("SELECT send_started_at, notified_at FROM notification_deliveries WHERE delivery_id='p0-owner-send'")
    ).one() == (None, None)
    assert db_session.execute(
        text("SELECT delivery_id FROM recommendation_history WHERE user_id=:u"), {"u": other}
    ).scalar_one() is None


def test_send_start_once_and_timeout_remains_unknown_and_unrecoverable(db_session, monkeypatch):
    owner, _, _ = _owner_and_other(db_session, monkeypatch)
    delivery_id = "p0-timeout-send"
    claim_notifications(NotificationClaimRequest(delivery_id=delivery_id), db_session)
    request = NotificationSendStartRequest(user_id=owner, delivery_id=delivery_id)
    first = start_notification_send(request, db_session)
    assert first.authorized is True
    assert first.send_started is True
    first_timestamp = db_session.execute(
        text("SELECT send_started_at FROM notification_deliveries WHERE delivery_id=:d"), {"d": delivery_id}
    ).scalar_one()

    failure = fail_notifications(
        NotificationFailRequest(user_id=owner, delivery_id=delivery_id, reason="ETIMEDOUT", definitive=False),
        db_session,
    )
    assert failure.released is False
    second = start_notification_send(request, db_session)
    assert second.authorized is False
    assert second.status == "already_started"
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at=clock_timestamp()-INTERVAL '2 hours' WHERE delivery_id=:d"),
        {"d": delivery_id},
    )
    db_session.commit()
    assert recover_stale_pre_send_claims(db_session, stale_minutes=30) == (0, 0)
    assert db_session.execute(
        text("SELECT send_started_at, notified_at FROM notification_deliveries WHERE delivery_id=:d"), {"d": delivery_id}
    ).one() == (first_timestamp, None)
    assert db_session.execute(
        text("SELECT COUNT(*) FROM recommendation_history WHERE delivery_id=:d"), {"d": delivery_id}
    ).scalar_one() == 1
