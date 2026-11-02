"""P0 coverage for live ownership, terminal fences, and paid receipts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import respx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User
from app.db.models.search_execution import SearchExecutionModel
from app.services.enrichment_worker import EnrichmentWorker
from app.services.firecrawl_quota import (
    get_reservation_receipt,
    mark_provider_request_in_flight,
    reconcile_stale_enrichment_reservations,
    settle_provider_reservation,
)
from app.services.ingestion import reserve_provider_request


def _configure_firecrawl(monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "p0-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)


def _job_with_lease(db_session, *, token: str, expires_at: datetime, status: str = "in_progress"):
    job = JobModel(
        title="Backend Engineer",
        company="P0 Company",
        source="p0_h20",
        source_job_id=f"p0-{token}",
        discovered_at=datetime.now(timezone.utc),
        description="A short search result snippet",
        description_is_snippet=True,
        url="https://example.com/jobs/p0",
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobEnrichmentModel(
            job_id=job.id,
            status=status,
            url=job.url,
            lease_holder="p0-worker" if status == "in_progress" else None,
            lease_token=token if status == "in_progress" else None,
            lease_expires_at=expires_at if status == "in_progress" else None,
        )
    )
    db_session.commit()
    return job.id


@pytest.mark.parametrize("terminal", [None, "failed"])
def test_reclaimed_dispatched_receipt_never_buys_again(db_session, monkeypatch, terminal):
    _configure_firecrawl(monkeypatch)
    jid = _job_with_lease(db_session, token="old", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    receipt = reserve_provider_request(db_session, "firecrawl_monthly", job_id=jid,
                                       lease_token="old", operation_key="crash-boundary")
    assert mark_provider_request_in_flight(receipt, db=db_session) is not None
    if terminal:
        settle_provider_reservation(receipt, terminal, db=db_session, metadata={"external_call": True})
    db_session.execute(text("UPDATE job_enrichments SET lease_token='new' WHERE job_id=:j"), {"j": jid})
    db_session.commit()
    for _ in range(2):
        assert reserve_provider_request(db_session, "firecrawl_monthly", job_id=jid,
                                        lease_token="new", operation_key="new-request") is None
    assert get_reservation_receipt(db_session, receipt.id).status == (terminal or "unknown_after_crash")
    assert db_session.execute(text("SELECT request_count FROM provider_usage WHERE provider_name='firecrawl_monthly'")).scalar_one() == 1


def test_settlement_exception_keeps_live_obligation_not_fallback(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    jid = _job_with_lease(db_session, token="settlement", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    worker = EnrichmentWorker()
    request = httpx.Request("GET", "https://example.com/jobs/p0")
    blocked = httpx.HTTPStatusError("blocked", request=request, response=httpx.Response(403, request=request))
    with patch.object(worker.ssrf_client, "fetch", side_effect=blocked), patch(
        "app.services.scraper.firecrawl.FirecrawlClient.scrape", return_value="too short"
    ) as paid, patch.object(worker, "_settle_receipt", return_value=None), patch.object(worker, "complete_with_snippet") as fallback:
        worker.process_job(db_session, jid, str(request.url), "settlement")
    paid.assert_called_once()
    fallback.assert_not_called()
    assert db_session.execute(text("SELECT status FROM provider_reservations WHERE job_id=:j"), {"j": jid}).scalar_one() == "in_flight"
    assert db_session.execute(text("SELECT status FROM job_enrichments WHERE job_id=:j"), {"j": jid}).scalar_one() == "in_progress"


def test_unsettled_paid_call_reconciles_after_lease_expiry_without_refund(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    jid = _job_with_lease(db_session, token="stale-settlement", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    receipt = reserve_provider_request(
        db_session,
        "firecrawl_monthly",
        job_id=jid,
        lease_token="stale-settlement",
        operation_key="stale-settlement-operation",
    )
    assert receipt is not None
    assert mark_provider_request_in_flight(receipt, db=db_session) is not None
    # Model a settlement failure followed by worker/lease expiry. The
    # reconciler accounts for the uncertain call rather than refunding it.
    db_session.execute(
        text("UPDATE job_enrichments SET lease_expires_at=clock_timestamp()-INTERVAL '1 minute' WHERE job_id=:j"),
        {"j": jid},
    )
    db_session.commit()

    assert reconcile_stale_enrichment_reservations(db_session, jid) == 1
    assert db_session.execute(
        text("SELECT status FROM provider_reservations WHERE id=:id"), {"id": receipt.id}
    ).scalar_one() == "unknown_after_crash"
    assert db_session.execute(
        text("SELECT status, reservation_id, job_id FROM firecrawl_operations WHERE operation_key=:key"),
        {"key": receipt.operation_key},
    ).one() == ("unknown_after_crash", receipt.id, jid)
    assert db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name='firecrawl_monthly'"),
    ).scalar_one() == 1


def test_exhausted_staged_recovery_retains_evidence(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    jid = _job_with_lease(db_session, token="staged", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    receipt = reserve_provider_request(db_session, "firecrawl_monthly", job_id=jid,
                                       lease_token="staged", operation_key="staged-exhaustion")
    assert mark_provider_request_in_flight(receipt, db=db_session)
    worker = EnrichmentWorker()
    assert worker._stage_paid_result(db_session, jid, "staged", receipt, "durable evidence")
    db_session.execute(text("UPDATE job_enrichments SET attempts=:n WHERE job_id=:j"), {"j": jid, "n": worker.max_attempts})
    db_session.commit()
    with patch.object(worker, "process_job") as process:
        worker.claim_and_process(db_session)
    process.assert_not_called()
    assert db_session.execute(text("SELECT status, staged_content, staged_reservation_id FROM job_enrichments WHERE job_id=:j"), {"j": jid}).one() == ("failure", "durable evidence", receipt.id)


def test_h20_expired_lease_makes_zero_native_or_paid_calls_and_side_effects(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    job_id = _job_with_lease(
        db_session,
        token="expired-token",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    worker = EnrichmentWorker()

    with patch.object(worker.ssrf_client, "fetch") as native_fetch, patch(
        "app.services.scraper.firecrawl.FirecrawlClient.scrape"
    ) as paid_scrape:
        worker.process_job(db_session, job_id, "https://example.com/jobs/p0", "expired-token")

    native_fetch.assert_not_called()
    paid_scrape.assert_not_called()
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_reservations WHERE job_id = :job_id"),
        {"job_id": job_id},
    ).scalar_one() == 0
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_usage WHERE provider_name = 'firecrawl_monthly'"),
    ).scalar_one() == 0


def test_h20_ownership_loss_before_reservation_has_zero_usage_and_no_receipt(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    token = "worker-a-token"
    job_id = _job_with_lease(
        db_session,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    # Worker B fences A before A reaches the independent reservation
    # transaction.  The stale token must not spend even one paid unit.
    db_session.execute(
        text("UPDATE job_enrichments SET lease_token = 'worker-b-token' WHERE job_id = :job_id"),
        {"job_id": job_id},
    )
    db_session.commit()

    receipt = reserve_provider_request(
        db_session,
        "firecrawl_monthly",
        cost_units=1,
        job_id=job_id,
        lease_token=token,
        operation_key="p0:h20:ownership-loss",
    )

    assert receipt is None
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_reservations WHERE operation_key = 'p0:h20:ownership-loss'"),
    ).scalar_one() == 0
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_usage WHERE provider_name = 'firecrawl_monthly'"),
    ).scalar_one() == 0


def test_worker_a_loses_to_worker_b_during_native_fetch_before_paid_reservation(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    job_id = _job_with_lease(
        db_session,
        token="worker-a",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    url = "https://example.com/jobs/p0"
    request = httpx.Request("GET", url)
    blocked = httpx.HTTPStatusError("blocked", request=request, response=httpx.Response(403, request=request))
    worker_a = EnrichmentWorker(worker_id="worker-a")
    worker_b = EnrichmentWorker(worker_id="worker-b")

    def lose_ownership(_url):
        with Session(db_session.get_bind()) as other:
            other.execute(
                text("UPDATE job_enrichments SET lease_holder='worker-b', lease_token='worker-b' WHERE job_id=:j"),
                {"j": job_id},
            )
            other.commit()
        raise blocked

    with patch.object(worker_a.ssrf_client, "fetch", side_effect=lose_ownership), patch(
        "app.services.scraper.firecrawl.FirecrawlClient.scrape"
    ) as scrape:
        worker_a.process_job(db_session, job_id, url, "worker-a")
        scrape.assert_not_called()
        assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 0
        assert db_session.execute(text("SELECT COUNT(*) FROM provider_usage WHERE provider_name='firecrawl_monthly'")).scalar_one() == 0
        assert db_session.execute(
            text("SELECT status, lease_token FROM job_enrichments WHERE job_id=:j"), {"j": job_id}
        ).one() == ("in_progress", "worker-b")

        scrape.return_value = "Responsibilities and requirements\n" + "Build reliable backend services. " * 20
        with patch.object(worker_b.ssrf_client, "fetch", side_effect=blocked):
            worker_b.process_job(db_session, job_id, url, "worker-b")
        scrape.assert_called_once_with(url)

    db_session.expire_all()
    assert db_session.execute(
        text("SELECT lease_token, status FROM provider_reservations WHERE job_id=:j"), {"j": job_id}
    ).one() == ("worker-b", "succeeded")
    assert db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name='firecrawl_monthly'")
    ).scalar_one() == 1


def test_terminal_replay_is_fenced_without_mutating_job_or_recommendations(db_session):
    job_id = _job_with_lease(
        db_session,
        token="terminal-token",
        expires_at=datetime.now(timezone.utc),
        status="success",
    )
    user = User(email="p0-terminal-owner@example.com", password_hash="test", is_active=True)
    db_session.add(user)
    db_session.flush()
    recommendation = RecommendationHistoryModel(user_id=user.id, job_id=job_id, score=70)
    db_session.add(recommendation)
    db_session.commit()
    before = db_session.execute(
        text("SELECT description, description_is_snippet FROM jobs WHERE id = :job_id"),
        {"job_id": job_id},
    ).one()

    valid_description = (
        "Responsibilities and requirements\n" + "Build backend services and maintain APIs. " * 20
    )
    with patch("app.services.enrichment_worker.calculate_match") as score, patch(
        "app.services.enrichment_worker.is_fresher_eligible"
    ) as eligibility:
        worker = EnrichmentWorker()
        worker.complete_success(db_session, job_id, "terminal-token", valid_description)
        worker.complete_with_snippet(db_session, job_id, "terminal-token", "late fallback")
    score.assert_not_called()
    eligibility.assert_not_called()

    after = db_session.execute(
        text("SELECT description, description_is_snippet FROM jobs WHERE id = :job_id"),
        {"job_id": job_id},
    ).one()
    status = db_session.execute(
        text("SELECT status, lease_token FROM job_enrichments WHERE job_id = :job_id"),
        {"job_id": job_id},
    ).one()
    assert after == before
    assert status == ("success", None)
    assert db_session.execute(
        text("SELECT score FROM recommendation_history WHERE job_id = :job_id"),
        {"job_id": job_id},
    ).scalar_one() == 70


def test_reservation_receipt_lifecycle_is_idempotent_and_unknown_is_fail_closed(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    token = "receipt-token"
    job_id = _job_with_lease(
        db_session,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    key = "p0:h20:receipt-lifecycle"

    receipt = reserve_provider_request(
        db_session,
        "firecrawl_monthly",
        cost_units=1,
        job_id=job_id,
        lease_token=token,
        operation_key=key,
    )
    replay = reserve_provider_request(
        db_session,
        "firecrawl_monthly",
        cost_units=1,
        job_id=job_id,
        lease_token=token,
        operation_key=key,
    )
    assert receipt is not None
    assert replay is not None
    assert replay.reservation_id == receipt.reservation_id
    assert db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly'"),
    ).scalar_one() == 1

    stored = get_reservation_receipt(db_session, receipt.reservation_id)
    assert stored is not None and stored.status == "reserved"
    # The lifecycle cannot skip dispatch and manufacture success.
    assert settle_provider_reservation(receipt, "succeeded", db=db_session).status == "reserved"
    in_flight = mark_provider_request_in_flight(receipt, db=db_session)
    assert in_flight is not None and in_flight.status == "in_flight"
    assert mark_provider_request_in_flight(receipt, db=db_session) is None

    unknown = settle_provider_reservation(
        receipt,
        "unknown_after_crash",
        db=db_session,
        metadata={"test": "ambiguous"},
    )
    assert unknown is not None and unknown.status == "unknown_after_crash"
    # A late worker cannot overwrite an accounted unknown outcome.
    assert settle_provider_reservation(receipt, "succeeded", db=db_session).status == "unknown_after_crash"
    replay_after_unknown = reserve_provider_request(
        db_session,
        "firecrawl_monthly",
        cost_units=1,
        job_id=job_id,
        lease_token=token,
        operation_key=key,
    )
    assert replay_after_unknown is not None
    assert replay_after_unknown.status == "unknown_after_crash"
    assert mark_provider_request_in_flight(replay_after_unknown, db=db_session) is None
    assert db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name='firecrawl_monthly'")
    ).scalar_one() == 1
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_reservations WHERE operation_key=:k"), {"k": key}
    ).scalar_one() == 1
    assert db_session.execute(
        text("SELECT COUNT(*), MIN(status) FROM firecrawl_operations WHERE reservation_id=:r"),
        {"r": receipt.reservation_id},
    ).one() == (1, "unknown_after_crash")


def test_unknown_receipt_id_never_authorizes_or_settles(db_session):
    from app.services.firecrawl_quota import ProviderReservationReceipt

    missing_id = "00000000-0000-0000-0000-000000000099"
    receipt = ProviderReservationReceipt(missing_id, "p0:missing", "firecrawl", 2)
    assert get_reservation_receipt(db_session, missing_id) is None
    assert mark_provider_request_in_flight(receipt, db=db_session) is None
    assert settle_provider_reservation(receipt, "succeeded", db=db_session) is None
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 0


def test_ownership_lost_after_reservation_cannot_dispatch_stale_receipt(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    job_id = _job_with_lease(
        db_session,
        token="before-dispatch-a",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    receipt = reserve_provider_request(
        db_session, "firecrawl_monthly", cost_units=1, job_id=job_id,
        lease_token="before-dispatch-a", operation_key="p0:dispatch-fence",
    )
    assert receipt is not None
    db_session.execute(
        text("UPDATE job_enrichments SET lease_token='before-dispatch-b' WHERE job_id=:j"), {"j": job_id}
    )
    db_session.commit()
    assert mark_provider_request_in_flight(receipt, db=db_session) is None
    assert get_reservation_receipt(db_session, receipt.reservation_id).status == "reserved"
    assert db_session.execute(
        text("SELECT SUM(request_count) FROM provider_usage WHERE provider_name='firecrawl_monthly'")
    ).scalar_one() == 1


def test_expired_search_execution_cannot_reserve_or_mutate_usage(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    execution = SearchExecutionModel(
        candidate_id="p0-expired-search", status="started", provider_name="firecrawl",
        selected_at=datetime.now(timezone.utc)-timedelta(hours=1),
        started_at=datetime.now(timezone.utc)-timedelta(hours=1),
    )
    db_session.add(execution)
    db_session.commit()
    assert reserve_provider_request(
        db_session, "firecrawl", cost_units=2, execution_id=execution.id,
        operation_key="p0:expired-search",
    ) is None
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_usage WHERE provider_name='firecrawl'")).scalar_one() == 0


def test_receipt_reservation_and_settlement_never_commit_caller_transaction(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    job_id = _job_with_lease(
        db_session, token="caller-transaction",
        expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),
    )
    pending = User(email="p0-uncommitted@example.com", password_hash="test", is_active=True)
    db_session.add(pending)
    db_session.flush()
    pending_id = pending.id
    receipt = reserve_provider_request(
        db_session, "firecrawl_monthly", cost_units=1, job_id=job_id,
        lease_token="caller-transaction", operation_key="p0:caller-transaction",
    )
    assert receipt is not None
    assert mark_provider_request_in_flight(receipt, db=db_session) is not None
    assert settle_provider_reservation(receipt, "succeeded", db=db_session).status == "succeeded"
    db_session.rollback()

    with Session(db_session.get_bind()) as fresh:
        assert fresh.get(User, pending_id) is None
        assert get_reservation_receipt(fresh, receipt.reservation_id).status == "succeeded"
        assert fresh.execute(text("SELECT SUM(request_count) FROM provider_usage WHERE provider_name='firecrawl_monthly'")).scalar_one() == 1


def test_real_scrape_402_settles_one_credit_and_latches_without_synthetic_usage(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    job_id = _job_with_lease(
        db_session,
        token="p0-real-scrape-402",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    url = "https://example.com/jobs/p0"
    request = httpx.Request("GET", url)
    blocked = httpx.HTTPStatusError("blocked", request=request, response=httpx.Response(403, request=request))
    worker = EnrichmentWorker()
    with respx.mock:
        route = respx.post("https://api.firecrawl.dev/v2/scrape").respond(
            402, json={"success": False, "error": "Payment Required"}
        )
        with patch.object(worker.ssrf_client, "fetch", side_effect=blocked):
            worker.process_job(db_session, job_id, url, "p0-real-scrape-402")
        assert route.call_count == 1
    assert db_session.execute(
        text("SELECT status, cost_units FROM provider_reservations WHERE job_id=:j"), {"j": job_id}
    ).one() == ("payment_required", 1)
    assert db_session.execute(text("SELECT payment_required FROM firecrawl_budget_state")).scalar_one() is True
    assert db_session.execute(
        text("SELECT SUM(request_count) FROM provider_usage WHERE provider_name='firecrawl_monthly'")
    ).scalar_one() == 1
    assert reserve_provider_request(db_session, "firecrawl", cost_units=2, operation_key="p0:after-payment") is None
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 1
