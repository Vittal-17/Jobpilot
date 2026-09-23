import pytest
import threading
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.db.models.job import JobModel
from app.db.models.user import User
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.notification_delivery import NotificationDeliveryModel
from app.api.endpoints.ingestion import (
    claim_notifications,
    NotificationClaimRequest,
    acknowledge_notifications,
    NotificationAcknowledgeRequest,
    start_notification_send,
    NotificationSendStartRequest,
    fail_notifications,
    NotificationFailRequest,
    recover_stale_pre_send_claims,
    recover_stale_notifications,
    NotificationRecoverStaleRequest,
)
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

def test_successful_acknowledgement(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    # 1. Claim notifications
    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_success_ack", limit=2)
    claim_resp = claim_notifications(claim_req, db_session)
    assert claim_resp.user_id == user_id
    assert len(claim_resp.recommendations) == 2

    # 2. Mark send started
    start_req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_success_ack")
    start_resp = start_notification_send(start_req, db_session)
    assert start_resp.send_started is True
    assert start_resp.authorized is True
    assert start_resp.status == "authorized"

    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_success_ack").first()
    assert delivery is not None
    assert delivery.send_started_at is not None
    assert delivery.notified_at is None

    # 3. Acknowledge delivery
    ack_req = NotificationAcknowledgeRequest(user_id=user_id, delivery_id="del_success_ack")
    ack_resp = acknowledge_notifications(ack_req, db_session)
    assert ack_resp.acknowledged is True

    db_session.refresh(delivery)
    assert delivery.notified_at is not None

    # Recommendations remain assigned to this delivery_id
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_success_ack").all()
    assert len(recs) == 2

def test_definitive_send_failure_release(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_def_fail", limit=2)
    claim_resp = claim_notifications(claim_req, db_session)
    assert len(claim_resp.recommendations) == 2

    start_req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_def_fail")
    start_notification_send(start_req, db_session)

    # Definitive failure (e.g. HTTP 400 / 403 rejection from Telegram)
    fail_req = NotificationFailRequest(
        user_id=user_id,
        delivery_id="del_def_fail",
        reason="Telegram 403: Bot was blocked by user",
        definitive=True,
    )
    fail_resp = fail_notifications(fail_req, db_session)
    assert fail_resp.released is True

    # Recommendations must be released back to the eligible pool (delivery_id IS NULL)
    recs = db_session.query(RecommendationHistoryModel).filter(
        RecommendationHistoryModel.job_id.in_([j1, j2])
    ).all()
    assert len(recs) == 2
    for r in recs:
        assert r.delivery_id is None

    # Delivery row must be removed
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_def_fail").first()
    assert delivery is None

    # A fresh claim can claim these recommendations again
    claim_resp2 = claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_def_retry", limit=2),
        db_session,
    )
    assert len(claim_resp2.recommendations) == 2

def test_ambiguous_send_failure_preservation(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_ambig_fail", limit=2)
    claim_resp = claim_notifications(claim_req, db_session)
    assert len(claim_resp.recommendations) == 2

    start_req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_ambig_fail")
    start_notification_send(start_req, db_session)

    # Ambiguous failure (e.g. timeout, network drop, 5xx): definitive=False
    fail_req = NotificationFailRequest(
        user_id=user_id,
        delivery_id="del_ambig_fail",
        reason="ECONNRESET or Gateway Timeout",
        definitive=False,
    )
    fail_resp = fail_notifications(fail_req, db_session)
    assert fail_resp.released is False
    assert "Ambiguous failure" in fail_resp.detail

    # Recommendations must NOT be released
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_ambig_fail").all()
    assert len(recs) == 2

    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_ambig_fail").first()
    assert delivery is not None
    assert delivery.send_started_at is not None
    assert delivery.notified_at is None

    # Stale claim recovery must NOT reclaim it because send has started
    recovered_dels, released_recs = recover_stale_pre_send_claims(db_session, stale_minutes=0)
    assert recovered_dels == 0
    assert released_recs == 0

def test_send_started_stale_claim_protection(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_started_prot", limit=2)
    claim_notifications(claim_req, db_session)

    start_req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_started_prot")
    start_notification_send(start_req, db_session)

    # Age claimed_at to 2 hours ago
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '2 hours' WHERE delivery_id = 'del_started_prot'")
    )
    db_session.commit()

    # Even though > 30 minutes old, send_started_at IS NOT NULL protects it from reclamation
    recovered_dels, released_recs = recover_stale_pre_send_claims(db_session, stale_minutes=30)
    assert recovered_dels == 0
    assert released_recs == 0

    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_started_prot").first()
    assert delivery is not None
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_started_prot").all()
    assert len(recs) == 2

def test_safe_pre_send_stranded_recovery(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_crashed_pre_send", limit=2)
    claim_notifications(claim_req, db_session)

    # Note: start_notification_send is NOT called (simulating worker crash before send starts)
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_crashed_pre_send").first()
    assert delivery.send_started_at is None
    assert delivery.notified_at is None

    # Age claimed_at to 2 hours ago
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '2 hours' WHERE delivery_id = 'del_crashed_pre_send'")
    )
    db_session.commit()

    # Pre-send stranded claim is safely recovered
    recovered_dels, released_recs = recover_stale_pre_send_claims(db_session, stale_minutes=30)
    db_session.commit()
    assert recovered_dels >= 1
    assert released_recs >= 2

    # Delivery row deleted, recommendations released back to eligible pool
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_crashed_pre_send").first()
    assert delivery is None

    recs = db_session.query(RecommendationHistoryModel).filter(
        RecommendationHistoryModel.job_id.in_([j1, j2])
    ).all()
    for r in recs:
        assert r.delivery_id is None

    # Fresh claim can re-claim them
    fresh_claim = claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_recovered_fresh", limit=2),
        db_session,
    )
    assert len(fresh_claim.recommendations) == 2

def test_duplicate_claim_prevention(db_session: Session):
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user = User(email="test_dup_prevention@example.com", is_active=True, password_hash="test")
    db_session.add(user)
    db_session.flush()

    jobs = [
        JobModel(title=f"Job {i}", company="C", source="adzuna", source_job_id=f"dup_{i}", discovered_at=datetime.now(timezone.utc))
        for i in range(4)
    ]
    db_session.add_all(jobs)
    db_session.flush()

    for j in jobs:
        db_session.add(RecommendationHistoryModel(user_id=user.id, job_id=j.id))
    db_session.commit()

    # 1. Sequential duplicate prevention: delivery 1 claims 2 jobs, delivery 2 gets remaining 2
    resp1 = claim_notifications(NotificationClaimRequest(user_id=user.id, delivery_id="del_seq_1", limit=2), db_session)
    assert len(resp1.recommendations) == 2

    resp2 = claim_notifications(NotificationClaimRequest(user_id=user.id, delivery_id="del_seq_2", limit=2), db_session)
    assert len(resp2.recommendations) == 2

    # Verify no overlap between deliveries
    set1 = {r.job_id for r in resp1.recommendations}
    set2 = {r.job_id for r in resp2.recommendations}
    assert set1.isdisjoint(set2)

    # 2. Concurrent duplicate prevention with row locking
    # Add 4 more recommendations
    jobs_more = [
        JobModel(title=f"Job More {i}", company="C", source="adzuna", source_job_id=f"dup_more_{i}", discovered_at=datetime.now(timezone.utc))
        for i in range(4)
    ]
    db_session.add_all(jobs_more)
    db_session.flush()
    for j in jobs_more:
        db_session.add(RecommendationHistoryModel(user_id=user.id, job_id=j.id))
    db_session.commit()

    s1 = SessionLocal()
    s2 = SessionLocal()
    barrier = threading.Barrier(2)
    results = {}

    def worker(s, d_id, key):
        try:
            barrier.wait(timeout=5)
            req = NotificationClaimRequest(user_id=user.id, delivery_id=d_id, limit=2)
            results[key] = claim_notifications(req, s)
        finally:
            s.close()

    t1 = threading.Thread(target=worker, args=(s1, "del_conc_1", "w1"))
    t2 = threading.Thread(target=worker, args=(s2, "del_conc_2", "w2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    res1_jobs = {r.job_id for r in results["w1"].recommendations}
    res2_jobs = {r.job_id for r in results["w2"].recommendations}
    assert res1_jobs.isdisjoint(res2_jobs), "Concurrent claims must never claim the same recommendation rows"

def test_same_delivery_acknowledgement_idempotency(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_ack_idemp", limit=2)
    claim_notifications(claim_req, db_session)

    start_req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_ack_idemp")
    start_notification_send(start_req, db_session)

    # First acknowledge
    ack_req = NotificationAcknowledgeRequest(user_id=user_id, delivery_id="del_ack_idemp")
    ack1 = acknowledge_notifications(ack_req, db_session)
    assert ack1.acknowledged is True

    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_ack_idemp").first()
    first_notified_at = delivery.notified_at
    assert first_notified_at is not None

    # Repeated acknowledge: must return acknowledged=True idempotently without error
    ack2 = acknowledge_notifications(ack_req, db_session)
    assert ack2.acknowledged is True

    db_session.refresh(delivery)
    assert delivery.notified_at == first_notified_at

    # Recommendations must remain safely intact
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_ack_idemp").all()
    assert len(recs) == 2

def test_multi_user_scoping(db_session: Session):
    # 1. Create two distinct active users
    user_a = User(email="user_a_scoped@example.com", is_active=True, password_hash="test")
    user_b = User(email="user_b_scoped@example.com", is_active=True, password_hash="test")
    db_session.add_all([user_a, user_b])
    db_session.flush()

    job_a = JobModel(title="Job A", company="Corp A", source="adzuna", source_job_id="scope_a", discovered_at=datetime.now(timezone.utc))
    job_b = JobModel(title="Job B", company="Corp B", source="adzuna", source_job_id="scope_b", discovered_at=datetime.now(timezone.utc))
    db_session.add_all([job_a, job_b])
    db_session.flush()

    rec_a = RecommendationHistoryModel(user_id=user_a.id, job_id=job_a.id)
    rec_b = RecommendationHistoryModel(user_id=user_b.id, job_id=job_b.id)
    db_session.add_all([rec_a, rec_b])
    db_session.commit()

    # 2. Dynamic user claim (user_id omitted) resolves user with oldest pending recommendation
    auto_claim_1 = claim_notifications(
        NotificationClaimRequest(delivery_id="del_scope_auto_1", limit=5),
        db_session,
    )
    assert auto_claim_1.user_id in (user_a.id, user_b.id)
    first_claimed_user = auto_claim_1.user_id
    assert len(auto_claim_1.recommendations) == 1

    # Second dynamic user claim resolves the other user
    auto_claim_2 = claim_notifications(
        NotificationClaimRequest(delivery_id="del_scope_auto_2", limit=5),
        db_session,
    )
    second_claimed_user = auto_claim_2.user_id
    assert second_claimed_user != first_claimed_user
    assert second_claimed_user in (user_a.id, user_b.id)
    assert len(auto_claim_2.recommendations) == 1

    # 3. Explicit user scoping: claim explicitly for user_a
    job_a2 = JobModel(title="Job A2", company="Corp A", source="adzuna", source_job_id="scope_a2", discovered_at=datetime.now(timezone.utc))
    db_session.add(job_a2)
    db_session.flush()
    db_session.add(RecommendationHistoryModel(user_id=user_a.id, job_id=job_a2.id))
    db_session.commit()

    explicit_claim_a = claim_notifications(
        NotificationClaimRequest(user_id=user_a.id, delivery_id="del_scope_explicit_a", limit=5),
        db_session,
    )
    assert explicit_claim_a.user_id == user_a.id
    assert len(explicit_claim_a.recommendations) == 1
    assert explicit_claim_a.recommendations[0].job_id == job_a2.id

    # 4. User scoping boundary on operations:
    # User B cannot start, fail, or acknowledge User A's delivery
    with pytest.raises(HTTPException) as excinfo:
        start_notification_send(
            NotificationSendStartRequest(user_id=user_b.id, delivery_id="del_scope_explicit_a"),
            db_session,
        )
    assert excinfo.value.status_code == 409

    with pytest.raises(HTTPException) as excinfo:
        acknowledge_notifications(
            NotificationAcknowledgeRequest(user_id=user_b.id, delivery_id="del_scope_explicit_a"),
            db_session,
        )
    assert excinfo.value.status_code == 409

    with pytest.raises(HTTPException) as excinfo:
        fail_notifications(
            NotificationFailRequest(user_id=user_b.id, delivery_id="del_scope_explicit_a", reason="test", definitive=True),
            db_session,
        )
    assert excinfo.value.status_code == 409

def test_recover_stale_notifications_endpoint(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_stale_endpoint", limit=2)
    claim_notifications(claim_req, db_session)

    # Pre-send: send_started_at is None
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_stale_endpoint").first()
    assert delivery is not None
    assert delivery.send_started_at is None

    # Age claimed_at to 45 minutes ago (> 30 min window)
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '45 minutes' WHERE delivery_id = 'del_stale_endpoint'")
    )
    db_session.commit()

    # Call dedicated recovery endpoint
    rec_req = NotificationRecoverStaleRequest(stale_minutes=30)
    rec_resp = recover_stale_notifications(rec_req, db_session)
    assert rec_resp.recovered_deliveries >= 1
    assert rec_resp.released_recommendations >= 2

    # Verify delivery deleted and recommendations released back to eligible pool
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_stale_endpoint").first()
    assert delivery is None

    recs = db_session.query(RecommendationHistoryModel).filter(
        RecommendationHistoryModel.job_id.in_([j1, j2])
    ).all()
    for r in recs:
        assert r.delivery_id is None

    # Idempotent: repeated call finds nothing more to recover
    rec_resp2 = recover_stale_notifications(rec_req, db_session)
    assert rec_resp2.recovered_deliveries == 0
    assert rec_resp2.released_recommendations == 0

def test_recover_stale_notifications_commits_when_no_active_users(db_session: Session):
    from sqlalchemy.orm import sessionmaker

    user_id, j1, j2 = setup_data(db_session)

    claim_req = NotificationClaimRequest(user_id=user_id, delivery_id="del_no_active_commit", limit=2)
    claim_notifications(claim_req, db_session)

    # Age to 45 minutes ago
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '45 minutes' WHERE delivery_id = 'del_no_active_commit'")
    )
    db_session.commit()

    # Deactivate all users so no active users exist in the database
    db_session.execute(text("UPDATE users SET is_active = false"))
    db_session.commit()

    # Verify claim returns empty because there are no active users
    empty_claim = claim_notifications(
        NotificationClaimRequest(delivery_id="del_no_active_attempt", limit=5),
        db_session,
    )
    assert empty_claim.recommendations == []

    # Dedicated recovery endpoint recovers and commits independently despite no active users
    rec_resp = recover_stale_notifications(NotificationRecoverStaleRequest(stale_minutes=30), db_session)
    assert rec_resp.recovered_deliveries >= 1
    assert rec_resp.released_recommendations >= 2

    # Open a completely fresh independent database session to verify storage commitment
    engine = db_session.get_bind()
    FreshSession = sessionmaker(bind=engine)
    with FreshSession() as fresh_s:
        d = fresh_s.query(NotificationDeliveryModel).filter_by(delivery_id="del_no_active_commit").first()
        assert d is None, "Delivery row must be committed as deleted"
        recs = fresh_s.query(RecommendationHistoryModel).filter(
            RecommendationHistoryModel.job_id.in_([j1, j2])
        ).all()
        for r in recs:
            assert r.delivery_id is None, "Recommendation delivery_id must be committed as NULL"

def test_concurrency_recovery_wins_send_start_loses(db_session: Session):
    import time
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_race_rec_wins", limit=2),
        db_session,
    )

    # Age claimed_at to 45 minutes ago
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '45 minutes' WHERE delivery_id = 'del_race_rec_wins'")
    )
    db_session.commit()

    s_rec = SessionLocal()
    s_send = SessionLocal()
    s_check = SessionLocal()

    lock_acquired = threading.Event()
    results = {}
    errors = {}

    pid_b = s_send.execute(text("SELECT pg_backend_pid()")).scalar()

    def worker_recovery():
        try:
            # 1. Session A holds row lock on stale delivery
            s_rec.execute(
                text("SELECT delivery_id FROM notification_deliveries WHERE delivery_id = 'del_race_rec_wins' FOR UPDATE")
            )
            lock_acquired.set()

            # 2. Synchronize on actual database blocking: poll pg_locks until Session B is blocked on lock
            blocked = False
            for _ in range(100):
                is_waiting = s_check.execute(
                    text("SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted"),
                    {"pid": pid_b}
                ).scalar()
                if is_waiting:
                    blocked = True
                    break
                time.sleep(0.02)

            assert blocked, "Session B must be verified as blocked on PostgreSQL lock"

            # 3. Complete recovery: release recommendations and delete delivery
            s_rec.execute(
                text("UPDATE recommendation_history SET delivery_id = NULL WHERE delivery_id = 'del_race_rec_wins'")
            )
            s_rec.execute(
                text("DELETE FROM notification_deliveries WHERE delivery_id = 'del_race_rec_wins'")
            )
            s_rec.commit()
            results["recovery"] = "committed"
        except Exception as e:
            errors["recovery"] = e
        finally:
            s_rec.close()

    def worker_send():
        try:
            lock_acquired.wait(timeout=5)
            # Will block behind Session A's lock, then unblock after Session A commits the deletion
            resp = start_notification_send(
                NotificationSendStartRequest(user_id=user_id, delivery_id="del_race_rec_wins"),
                s_send,
            )
            results["send"] = resp
        except Exception as e:
            errors["send"] = e
        finally:
            s_send.close()

    t_rec = threading.Thread(target=worker_recovery)
    t_send = threading.Thread(target=worker_send)

    t_rec.start()
    t_send.start()

    t_rec.join(timeout=10)
    t_send.join(timeout=10)
    s_check.close()

    # Both threads must terminate without hangs
    assert not t_rec.is_alive(), "Recovery thread hung"
    assert not t_send.is_alive(), "Send-start thread hung"
    assert "recovery" not in errors, f"Recovery raised unexpected error: {errors.get('recovery')}"
    assert results.get("recovery") == "committed"

    # Send-start lost and received HTTP 409
    assert "send" not in results, "Send-start must not succeed after delivery is reclaimed"
    assert "send" in errors, "Send-start thread must record an exception"
    assert isinstance(errors["send"], HTTPException)
    assert errors["send"].status_code == 409
    assert errors["send"].detail == "Delivery claim expired or reclaimed; cannot start send"

    # Verify database state: delivery row deleted, recommendations released back to eligible pool
    assert db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_race_rec_wins").first() is None
    recs = db_session.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.job_id.in_([j1, j2])).all()
    assert all(r.delivery_id is None for r in recs)

    # Prove recommendations are now eligible to be claimed again
    fresh_claim = claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_after_race_retry", limit=2),
        db_session,
    )
    assert len(fresh_claim.recommendations) == 2

def test_concurrency_send_start_wins_stale_recovery_skips(db_session: Session):
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_race_send_wins", limit=2),
        db_session,
    )

    # Age claimed_at to 45 minutes ago
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '45 minutes' WHERE delivery_id = 'del_race_send_wins'")
    )
    db_session.commit()

    s_send = SessionLocal()
    s_rec = SessionLocal()

    send_locked = threading.Event()
    results = {}
    errors = {}

    def worker_send():
        try:
            # 1. Send-start acquires row lock first
            s_send.execute(
                text("SELECT delivery_id FROM notification_deliveries WHERE delivery_id = 'del_race_send_wins' FOR UPDATE")
            )
            send_locked.set()

            # Mark send-started and commit
            s_send.execute(
                text("UPDATE notification_deliveries SET send_started_at = clock_timestamp() WHERE delivery_id = 'del_race_send_wins'")
            )
            s_send.commit()
            results["send"] = "committed"
        except Exception as e:
            errors["send"] = e
        finally:
            s_send.close()

    def worker_recovery():
        try:
            send_locked.wait(timeout=5)
            # Recovery executes FOR UPDATE SKIP LOCKED: skips the locked row
            recovered, released = recover_stale_pre_send_claims(s_rec, stale_minutes=30)
            s_rec.commit()
            results["recovery"] = (recovered, released)
        except Exception as e:
            errors["recovery"] = e
        finally:
            s_rec.close()

    t_send = threading.Thread(target=worker_send)
    t_rec = threading.Thread(target=worker_recovery)

    t_send.start()
    t_rec.start()

    t_send.join(timeout=10)
    t_rec.join(timeout=10)

    assert not t_send.is_alive(), "Send-start thread hung"
    assert not t_rec.is_alive(), "Recovery thread hung"
    assert "send" not in errors, f"Send-start error: {errors.get('send')}"
    assert "recovery" not in errors, f"Recovery error: {errors.get('recovery')}"

    # Recovery skipped the send-started delivery (0 recovered, 0 released)
    assert results.get("recovery") == (0, 0)
    assert results.get("send") == "committed"

    # Delivery row remains intact with send_started_at populated
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_race_send_wins").first()
    assert delivery is not None
    assert delivery.send_started_at is not None

    # Recommendations remain claimed under del_race_send_wins
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_race_send_wins").all()
    assert len(recs) == 2

def test_repeated_send_start_same_delivery(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_repeat_start", limit=2),
        db_session,
    )

    # 1. First send-start: must authorize
    req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_repeat_start")
    resp1 = start_notification_send(req, db_session)
    assert resp1.authorized is True
    assert resp1.send_started is True
    assert resp1.status == "authorized"

    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_repeat_start").first()
    assert delivery is not None
    initial_started_at = delivery.send_started_at
    assert initial_started_at is not None

    # 2. Second send-start on same delivery: must NOT authorize
    resp2 = start_notification_send(req, db_session)
    assert resp2.authorized is False
    assert resp2.send_started is False
    assert resp2.status == "already_started"

    # 3. Third send-start: still NOT authorize
    resp3 = start_notification_send(req, db_session)
    assert resp3.authorized is False
    assert resp3.send_started is False
    assert resp3.status == "already_started"

    # Timestamp in DB must remain unchanged
    db_session.refresh(delivery)
    assert delivery.send_started_at == initial_started_at

def test_send_start_after_acknowledgement(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_start_after_ack", limit=2),
        db_session,
    )

    # 1. First send-start: authorizes
    start_req = NotificationSendStartRequest(user_id=user_id, delivery_id="del_start_after_ack")
    start_resp = start_notification_send(start_req, db_session)
    assert start_resp.authorized is True
    assert start_resp.status == "authorized"

    # 2. Acknowledge delivery
    ack_req = NotificationAcknowledgeRequest(user_id=user_id, delivery_id="del_start_after_ack")
    ack_resp = acknowledge_notifications(ack_req, db_session)
    assert ack_resp.acknowledged is True

    # 3. Attempt send-start after acknowledgement: must NOT authorize
    start_resp2 = start_notification_send(start_req, db_session)
    assert start_resp2.authorized is False
    assert start_resp2.send_started is False
    assert start_resp2.status == "already_acknowledged"

    # Ensure recommendations and acknowledged state are intact
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_start_after_ack").first()
    assert delivery.notified_at is not None
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_start_after_ack").all()
    assert len(recs) == 2

def test_concurrent_duplicate_send_start(db_session: Session):
    import time
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_conc_start_test", limit=2),
        db_session,
    )

    s_a = SessionLocal()
    s_b = SessionLocal()
    s_check = SessionLocal()

    lock_acquired = threading.Event()
    results = {}
    errors = {}

    pid_b = s_b.execute(text("SELECT pg_backend_pid()")).scalar()

    def worker_a():
        try:
            # 1. Session A acquires exclusive row lock on the delivery
            s_a.execute(
                text("SELECT delivery_id FROM notification_deliveries WHERE delivery_id = 'del_conc_start_test' FOR UPDATE")
            )
            lock_acquired.set()

            # 2. Wait until Session B is verified as blocked on pg_locks
            blocked = False
            for _ in range(100):
                is_waiting = s_check.execute(
                    text("SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted"),
                    {"pid": pid_b}
                ).scalar()
                if is_waiting:
                    blocked = True
                    break
                time.sleep(0.02)

            assert blocked, "Session B must be verified as blocked on PostgreSQL row lock"

            # 3. Session A executes atomic send-start transition and commits
            s_a.execute(
                text("""
                UPDATE notification_deliveries
                SET send_started_at = clock_timestamp()
                WHERE delivery_id = 'del_conc_start_test'
                  AND user_id = :user_id
                  AND send_started_at IS NULL
                """),
                {"user_id": user_id}
            )
            s_a.commit()
            results["session_a"] = "committed"
        except Exception as e:
            errors["session_a"] = e
        finally:
            s_a.close()

    def worker_b():
        try:
            lock_acquired.wait(timeout=5)
            # Session B calls start_notification_send; enters SELECT ... FOR UPDATE and blocks in PostgreSQL kernel
            resp = start_notification_send(
                NotificationSendStartRequest(user_id=user_id, delivery_id="del_conc_start_test"),
                s_b,
            )
            results["session_b"] = resp
        except Exception as e:
            errors["session_b"] = e
        finally:
            s_b.close()

    t_a = threading.Thread(target=worker_a)
    t_b = threading.Thread(target=worker_b)

    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)
    s_check.close()

    assert not t_a.is_alive(), "Worker A hung"
    assert not t_b.is_alive(), "Worker B hung"
    assert len(errors) == 0, f"Unexpected errors during concurrent send-start: {errors}"
    assert results.get("session_a") == "committed"

    # Session B unblocks after Session A commits, observes send_started_at IS NOT NULL, and returns authorized=False
    resp_b = results.get("session_b")
    assert resp_b is not None
    assert resp_b.authorized is False
    assert resp_b.send_started is False
    assert resp_b.status == "already_started"

    # Verify database state has exactly one send_started_at timestamp
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_conc_start_test").first()
    assert delivery is not None
    assert delivery.send_started_at is not None
    assert delivery.notified_at is None

def test_reclaimed_send_start_fails_closed_409(db_session: Session):
    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_reclaim_then_start", limit=2),
        db_session,
    )

    # Age claim past stale threshold
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '45 minutes' WHERE delivery_id = 'del_reclaim_then_start'")
    )
    db_session.commit()

    # Recovery reclaims and deletes the delivery
    recovered, released = recover_stale_pre_send_claims(db_session, stale_minutes=30)
    db_session.commit()
    assert recovered == 1
    assert released == 2

    # Send-start must fail-closed with HTTP 409
    with pytest.raises(HTTPException) as excinfo:
        start_notification_send(
            NotificationSendStartRequest(user_id=user_id, delivery_id="del_reclaim_then_start"),
            db_session,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "Delivery claim expired or reclaimed; cannot start send"

def test_concurrent_acknowledgement_reclaim_correctness(db_session: Session):
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_ack_reclaim_race", limit=2),
        db_session,
    )

    # Age claim past 30 min
    db_session.execute(
        text("UPDATE notification_deliveries SET claimed_at = clock_timestamp() - INTERVAL '45 minutes' WHERE delivery_id = 'del_ack_reclaim_race'")
    )
    db_session.commit()

    # Scenario: Recovery deletes the row before acknowledgement acquires lock
    s_rec = SessionLocal()
    recovered, released = recover_stale_pre_send_claims(s_rec, stale_minutes=30)
    s_rec.commit()
    s_rec.close()
    assert recovered == 1

    # Acknowledge on the reclaimed delivery must NOT falsely report success
    with pytest.raises(HTTPException) as excinfo:
        acknowledge_notifications(
            NotificationAcknowledgeRequest(user_id=user_id, delivery_id="del_ack_reclaim_race"),
            db_session,
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Delivery not found"

def test_concurrency_acknowledge_wins_fail_blocked(db_session: Session):
    import time
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_race_ack_wins", limit=2),
        db_session,
    )
    # Mark send-started
    start_notification_send(
        NotificationSendStartRequest(user_id=user_id, delivery_id="del_race_ack_wins"),
        db_session,
    )

    s_ack = SessionLocal()
    s_fail = SessionLocal()
    s_check = SessionLocal()

    lock_acquired = threading.Event()
    results = {}
    errors = {}

    pid_fail = s_fail.execute(text("SELECT pg_backend_pid()")).scalar()

    def worker_ack():
        try:
            # 1. Session Ack acquires row lock on the delivery
            s_ack.execute(
                text("SELECT delivery_id FROM notification_deliveries WHERE delivery_id = 'del_race_ack_wins' FOR UPDATE")
            )
            lock_acquired.set()

            # 2. Wait until Session Fail is verified as blocked on pg_locks
            blocked = False
            for _ in range(100):
                is_waiting = s_check.execute(
                    text("SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted"),
                    {"pid": pid_fail}
                ).scalar()
                if is_waiting:
                    blocked = True
                    break
                time.sleep(0.02)

            assert blocked, "Session Fail must be verified as blocked on PostgreSQL row lock"

            # 3. Session Ack completes acknowledgement and commits
            s_ack.execute(
                text("""
                UPDATE notification_deliveries
                SET notified_at = CURRENT_TIMESTAMP
                WHERE delivery_id = 'del_race_ack_wins'
                  AND user_id = :user_id
                  AND notified_at IS NULL
                """),
                {"user_id": user_id}
            )
            s_ack.commit()
            results["ack"] = "committed"
        except Exception as e:
            errors["ack"] = e
        finally:
            s_ack.close()

    def worker_fail():
        try:
            lock_acquired.wait(timeout=5)
            # Session Fail calls fail_notifications(definitive=True); blocks behind Session Ack's row lock
            resp = fail_notifications(
                NotificationFailRequest(
                    user_id=user_id,
                    delivery_id="del_race_ack_wins",
                    reason="Telegram rejection race",
                    definitive=True,
                ),
                s_fail,
            )
            results["fail"] = resp
        except Exception as e:
            errors["fail"] = e
        finally:
            s_fail.close()

    t_ack = threading.Thread(target=worker_ack)
    t_fail = threading.Thread(target=worker_fail)

    t_ack.start()
    t_fail.start()
    t_ack.join(timeout=10)
    t_fail.join(timeout=10)
    s_check.close()

    assert not t_ack.is_alive(), "Worker Ack hung"
    assert not t_fail.is_alive(), "Worker Fail hung"
    assert len(errors) == 0, f"Unexpected errors during ack-wins race: {errors}"
    assert results.get("ack") == "committed"

    # Session Fail unblocks after Session Ack commits, observes notified_at IS NOT NULL,
    # and returns released=False without deleting or releasing anything
    fail_resp = results.get("fail")
    assert fail_resp is not None
    assert fail_resp.released is False
    assert "already acknowledged" in fail_resp.detail

    # Verify persisted state: delivery row remains intact with notified_at set
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_race_ack_wins").first()
    assert delivery is not None
    assert delivery.notified_at is not None

    # Recommendations remain assigned to del_race_ack_wins
    recs = db_session.query(RecommendationHistoryModel).filter_by(delivery_id="del_race_ack_wins").all()
    assert len(recs) == 2

def test_concurrency_definitive_fail_wins_acknowledge_blocked(db_session: Session):
    import time
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    user_id, j1, j2 = setup_data(db_session)
    claim_notifications(
        NotificationClaimRequest(user_id=user_id, delivery_id="del_race_fail_wins", limit=2),
        db_session,
    )
    start_notification_send(
        NotificationSendStartRequest(user_id=user_id, delivery_id="del_race_fail_wins"),
        db_session,
    )

    s_fail = SessionLocal()
    s_ack = SessionLocal()
    s_check = SessionLocal()

    lock_acquired = threading.Event()
    results = {}
    errors = {}

    pid_ack = s_ack.execute(text("SELECT pg_backend_pid()")).scalar()

    def worker_fail():
        try:
            # 1. Session Fail acquires row lock on the delivery
            s_fail.execute(
                text("SELECT delivery_id FROM notification_deliveries WHERE delivery_id = 'del_race_fail_wins' FOR UPDATE")
            )
            lock_acquired.set()

            # 2. Wait until Session Ack is verified as blocked on pg_locks
            blocked = False
            for _ in range(100):
                is_waiting = s_check.execute(
                    text("SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted"),
                    {"pid": pid_ack}
                ).scalar()
                if is_waiting:
                    blocked = True
                    break
                time.sleep(0.02)

            assert blocked, "Session Ack must be verified as blocked on PostgreSQL row lock"

            # 3. Session Fail releases recommendations and deletes delivery row, then commits
            s_fail.execute(
                text("UPDATE recommendation_history SET delivery_id = NULL WHERE delivery_id = 'del_race_fail_wins' AND user_id = :user_id"),
                {"user_id": user_id}
            )
            s_fail.execute(
                text("DELETE FROM notification_deliveries WHERE delivery_id = 'del_race_fail_wins' AND user_id = :user_id"),
                {"user_id": user_id}
            )
            s_fail.commit()
            results["fail"] = "committed"
        except Exception as e:
            errors["fail"] = e
        finally:
            s_fail.close()

    def worker_ack():
        try:
            lock_acquired.wait(timeout=5)
            # Session Ack calls acknowledge_notifications; blocks behind Session Fail's row lock
            resp = acknowledge_notifications(
                NotificationAcknowledgeRequest(user_id=user_id, delivery_id="del_race_fail_wins"),
                s_ack,
            )
            results["ack"] = resp
        except Exception as e:
            errors["ack"] = e
        finally:
            s_ack.close()

    t_fail = threading.Thread(target=worker_fail)
    t_ack = threading.Thread(target=worker_ack)

    t_fail.start()
    t_ack.start()
    t_fail.join(timeout=10)
    t_ack.join(timeout=10)
    s_check.close()

    assert not t_fail.is_alive(), "Worker Fail hung"
    assert not t_ack.is_alive(), "Worker Ack hung"
    assert "fail" not in errors, f"Session Fail error: {errors.get('fail')}"
    assert results.get("fail") == "committed"

    # Session Ack must unblock after Session Fail commits deletion, find no row,
    # and fail with HTTP 404 without ever reporting acknowledged=True
    assert "ack" not in results, "Session Ack must NOT succeed after delivery is deleted"
    assert "ack" in errors, "Session Ack must raise an error"
    assert isinstance(errors["ack"], HTTPException)
    assert errors["ack"].status_code == 404
    assert errors["ack"].detail == "Delivery not found"

    # Verify persisted state: delivery row deleted
    delivery = db_session.query(NotificationDeliveryModel).filter_by(delivery_id="del_race_fail_wins").first()
    assert delivery is None

    # Recommendations are cleanly released back to eligible pool (delivery_id is NULL)
    recs = db_session.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.job_id.in_([j1, j2])).all()
    assert all(r.delivery_id is None for r in recs)
