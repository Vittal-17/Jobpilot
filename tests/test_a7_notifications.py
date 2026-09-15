import pytest
import threading
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.models.job import JobModel
from app.db.models.user import User
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.notification_delivery import NotificationDeliveryModel
from app.api.endpoints.ingestion import claim_notifications, NotificationClaimRequest, acknowledge_notifications, NotificationAcknowledgeRequest
from fastapi import HTTPException

def setup_data(db_session: Session):
    user = User(email="test_a7_final@example.com", is_active=True, password_hash="test")
    db_session.add(user)
    db_session.flush()

    job1 = JobModel(title="J1", company="C", source="adzuna", source_job_id="1", discovered_at=datetime.now(timezone.utc))
    job2 = JobModel(title="J2", company="C", source="adzuna", source_job_id="2", discovered_at=datetime.now(timezone.utc))
    db_session.add(job1)
    db_session.add(job2)
    db_session.flush()

    r1 = RecommendationHistoryModel(user_id=user.id, job_id=job1.id)
    r2 = RecommendationHistoryModel(user_id=user.id, job_id=job2.id)
    db_session.add(r1)
    db_session.add(r2)
    db_session.commit()
    return user.id, job1.id, job2.id

def test_claim_and_acknowledge_state_semantics(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    req = NotificationClaimRequest(user_id=user_id, delivery_id="del_1", limit=1)
    resp = claim_notifications(req, db_session)
    assert len(resp.recommendations) == 1

    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_1").first()
    assert delivery is not None
    assert delivery.notified_at is None

    rec = db_session.query(RecommendationHistoryModel).filter_by(job_id=resp.recommendations[0].job_id).first()
    assert rec.delivery_id == "del_1"

    ack_req = NotificationAcknowledgeRequest(user_id=user_id, delivery_id="del_1")
    ack_resp = acknowledge_notifications(ack_req, db_session)
    assert ack_resp.acknowledged is True

    db_session.refresh(delivery)
    assert delivery.notified_at is not None

def test_idempotent_retry_semantics(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    req = NotificationClaimRequest(user_id=user_id, delivery_id="del_retry", limit=2)
    resp1 = claim_notifications(req, db_session)
    assert len(resp1.recommendations) == 2

    resp2 = claim_notifications(req, db_session)
    assert len(resp2.recommendations) == 2
    assert resp1.recommendations[0].job_id == resp2.recommendations[0].job_id
    assert resp1.recommendations[1].job_id == resp2.recommendations[1].job_id

def test_duplicate_same_delivery_id_requests_concurrent(db_session: Session):
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)

    s1 = SessionLocal()
    s2 = SessionLocal()

    barrier = threading.Barrier(2)
    res = {}

    def worker1():
        try:
            barrier.wait(timeout=5)
            req = NotificationClaimRequest(user_id=user_id, delivery_id="conc_same", limit=1)
            resp = claim_notifications(req, s1)
            res["w1"] = resp
        finally:
            s1.close()

    def worker2():
        try:
            barrier.wait(timeout=5)
            req = NotificationClaimRequest(user_id=user_id, delivery_id="conc_same", limit=1)
            resp = claim_notifications(req, s2)
            res["w2"] = resp
        finally:
            s2.close()

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    r1 = res["w1"]
    r2 = res["w2"]

    assert len(r1.recommendations) == 1
    assert len(r2.recommendations) == 1
    assert r1.recommendations[0].job_id == r2.recommendations[0].job_id

def test_cross_user_delivery_id_collision(db_session: Session):
    user_id1, j1, j2 = setup_data(db_session)

    user2 = User(email="test_a7_cross@example.com", is_active=True, password_hash="test")
    db_session.add(user2)
    db_session.commit()
    user_id2 = user2.id

    # User 1 claims a delivery_id
    req1 = NotificationClaimRequest(user_id=user_id1, delivery_id="shared_del_id", limit=1)
    claim_notifications(req1, db_session)

    # User 2 tries to use the same delivery_id
    req2 = NotificationClaimRequest(user_id=user_id2, delivery_id="shared_del_id", limit=1)
    with pytest.raises(HTTPException) as excinfo:
        claim_notifications(req2, db_session)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "delivery_id conflict"
