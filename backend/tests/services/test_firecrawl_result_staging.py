from datetime import datetime, timezone
from datetime import timedelta
from threading import Event, Thread
import time

import pytest

from app.models.job import Job
from app.schemas.job_search import JobSearchQuery
from app.services.ingestion import ProviderSettlementError, run_ingestion
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models.execution_outcome import FirecrawlDiscoveryRunModel
from app.db.models.search_execution import SearchExecutionModel
from unittest.mock import MagicMock, patch
from sqlalchemy import text
from app.services.firecrawl_result_staging import (
    FirecrawlOperationConflict,
    FirecrawlResultError,
    RecoveryResult,
    deserialize_jobs,
    recover_staged_firecrawl_result,
    request_fingerprint,
    serialize_jobs,
    stage_firecrawl_result,
)
from app.services.firecrawl_quota import (
    assert_discovery_ownership,
    mark_provider_request_in_flight,
    reconcile_stale_discovery_reservations,
    reserve_provider_request,
)


def _job(i: int = 1) -> Job:
    return Job(
        title=f"Engineer {i}", company="Example", source="firecrawl",
        source_job_id=f"job-{i}", discovered_at=datetime.now(timezone.utc),
        description="snippet", location="Bengaluru",
    )


def test_staged_job_payload_round_trips_with_integrity_metadata():
    staged = serialize_jobs([_job()])
    jobs = deserialize_jobs(
        staged.payload,
        sha256=staged.sha256,
        byte_count=staged.byte_count,
        job_count=staged.job_count,
    )
    assert jobs[0].source_job_id == "job-1"
    assert staged.byte_count <= 1024 * 1024


def test_staged_job_payload_rejects_tampering_and_oversize():
    staged = serialize_jobs([_job()])
    with pytest.raises(FirecrawlResultError):
        deserialize_jobs(staged.payload, sha256="0" * 64, byte_count=staged.byte_count, job_count=1)
    with pytest.raises(FirecrawlResultError):
        serialize_jobs([_job(i) for i in range(101)])


def test_staged_job_payload_drops_provider_extra_fields():
    job = _job().model_dump(mode="json")
    job["raw_response"] = "secret"
    with pytest.raises(FirecrawlResultError):
        serialize_jobs([job])


def test_request_fingerprint_binds_query_and_execution_identity():
    query = JobSearchQuery(keywords="Python", location="Bengaluru", radius_km=None, page=1, page_size=10)
    assert request_fingerprint(provider_name="firecrawl", query=query, execution_id=None) != request_fingerprint(
        provider_name="firecrawl", query=query, execution_id=42,
    )
    assert request_fingerprint(provider_name="firecrawl", query=query, execution_id=None) != request_fingerprint(
        provider_name="firecrawl",
        query=query.model_copy(update={"page": 2}),
        execution_id=None,
    )


def test_direct_idempotency_key_recovers_staged_result_without_provider_call(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    query = JobSearchQuery(keywords="Python", location="Bengaluru", radius_km=None, page=1, page_size=10)
    key = "direct:staged-recovery"
    provider = MagicMock()
    provider.search_jobs.return_value = [_job()]
    with patch("app.services.ingestion._settle_provider_receipt", side_effect=ProviderSettlementError("injected")):
        with pytest.raises(ProviderSettlementError):
            run_ingestion(db_session, "firecrawl", provider, query, operation_key=key)
    assert db_session.execute(text("SELECT result_state FROM firecrawl_operations WHERE operation_key=:key"), {"key": key}).scalar_one() == "staged"
    provider.search_jobs.reset_mock()
    result, ids = run_ingestion(db_session, "firecrawl", provider, query, operation_key=key)
    assert result.fetched == 1
    assert ids
    provider.search_jobs.assert_not_called()
    assert db_session.execute(text("SELECT result_state FROM firecrawl_operations WHERE operation_key=:key"), {"key": key}).scalar_one() == "consumed"


def test_crash_after_settlement_replays_without_provider_call(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    query = JobSearchQuery(keywords="Python", location="Bengaluru", radius_km=None, page=1, page_size=10)
    key = "direct:after-settlement"
    provider = MagicMock()
    provider.search_jobs.return_value = [_job()]
    calls = 0

    def crash_after_settlement(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return RecoveryResult(found=False)
        raise SystemExit("crash after settlement")

    with patch(
        "app.services.firecrawl_result_staging.recover_staged_firecrawl_result",
        side_effect=crash_after_settlement,
    ):
        with pytest.raises(SystemExit, match="after settlement"):
            run_ingestion(db_session, "firecrawl", provider, query, operation_key=key)

    assert db_session.execute(
        text("SELECT status, result_state FROM firecrawl_operations WHERE operation_key=:key"),
        {"key": key},
    ).one() == ("success", "staged")
    provider.search_jobs.reset_mock()
    recovered, ids = run_ingestion(db_session, "firecrawl", provider, query, operation_key=key)
    assert recovered.created == 1
    assert ids
    provider.search_jobs.assert_not_called()
    assert db_session.execute(
        text("SELECT result_state FROM firecrawl_operations WHERE operation_key=:key"),
        {"key": key},
    ).scalar_one() == "consumed"


def test_crash_before_job_persistence_commit_replays_idempotently(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    query = JobSearchQuery(keywords="Python", location="Bengaluru", radius_km=None, page=1, page_size=10)
    key = "direct:before-job-commit"
    provider = MagicMock()
    provider.search_jobs.return_value = [_job()]

    with patch("app.services.firecrawl_result_staging.save_job", side_effect=SystemExit("before job commit")):
        with pytest.raises(SystemExit, match="before job commit"):
            run_ingestion(db_session, "firecrawl", provider, query, operation_key=key)

    assert db_session.execute(
        text("SELECT status, result_state FROM firecrawl_operations WHERE operation_key=:key"),
        {"key": key},
    ).one() == ("success", "staged")
    provider.search_jobs.reset_mock()
    recovered, ids = run_ingestion(db_session, "firecrawl", provider, query, operation_key=key)
    assert recovered.created == 1
    assert ids
    provider.search_jobs.assert_not_called()
    assert db_session.execute(
        text("SELECT COUNT(*) FROM jobs WHERE source_job_id='job-1'"),
    ).scalar_one() == 1


def test_same_idempotency_key_different_query_fails_without_mutation(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    first_query = JobSearchQuery(keywords="Python", location="Bengaluru", radius_km=None, page=1, page_size=10)
    conflicting_query = first_query.model_copy(update={"keywords": "Django"})
    key = "direct:fingerprint-conflict"
    provider = MagicMock()
    provider.search_jobs.return_value = [_job()]

    result, ids = run_ingestion(db_session, "firecrawl", provider, first_query, operation_key=key)
    assert result.created == 1
    assert ids
    job_count = db_session.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one()
    provider.search_jobs.reset_mock()

    with pytest.raises(FirecrawlOperationConflict):
        run_ingestion(db_session, "firecrawl", provider, conflicting_query, operation_key=key)

    provider.search_jobs.assert_not_called()
    assert db_session.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == job_count
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 1
    assert db_session.execute(text("SELECT COUNT(*) FROM search_execution")).scalar_one() == 0


def test_staging_rejects_mismatched_reservation_receipt(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    first = reserve_provider_request(db_session, "firecrawl", cost_units=2, operation_key="stage:one")
    second = reserve_provider_request(db_session, "firecrawl", cost_units=2, operation_key="stage:two")
    assert first is not None and second is not None
    assert mark_provider_request_in_flight(first, db=db_session) is not None
    with pytest.raises(FirecrawlResultError, match="does not match"):
        stage_firecrawl_result(
            db_session,
            operation_key="stage:one",
            operation="discovery",
            cost_units=2,
            execution_id=None,
            reservation_id=second.reservation_id,
            jobs=[_job()],
        )


def test_database_rejects_staged_operation_without_reservation(db_session):
    with pytest.raises(Exception):
        db_session.execute(text("""
            INSERT INTO firecrawl_operations
                (operation, cost_units, status, operation_key, result_state,
                 staged_payload, staged_payload_sha256, staged_payload_bytes,
                 staged_job_count, staged_at)
            VALUES
                ('discovery', 2, 'success', 'stage:missing-receipt', 'staged',
                 '{"version": 1, "jobs": []}'::jsonb, :sha, 25, 0,
                 clock_timestamp())
        """), {"sha": "0" * 64})
        db_session.commit()
    db_session.rollback()


def test_database_rejects_deleting_receipt_for_consumed_operation(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    provider = MagicMock()
    provider.search_jobs.return_value = []
    query = JobSearchQuery(keywords="Python", location="Bengaluru", radius_km=None, page=1, page_size=10)
    run_ingestion(db_session, "firecrawl", provider, query, operation_key="stage:delete-receipt")
    reservation_id = db_session.execute(text(
        "SELECT reservation_id FROM firecrawl_operations WHERE operation_key='stage:delete-receipt'"
    )).scalar_one()
    with pytest.raises(Exception):
        db_session.execute(text(
            "DELETE FROM provider_reservations WHERE id=:reservation_id"
        ), {"reservation_id": reservation_id})
        db_session.commit()
    db_session.rollback()
    assert db_session.execute(text(
        "SELECT result_state FROM firecrawl_operations WHERE operation_key='stage:delete-receipt'"
    )).scalar_one() == "consumed"


def test_recovery_missing_receipt_fails_closed(db_session, monkeypatch):
    staged = serialize_jobs([])
    row = {
        "operation": "discovery",
        "cost_units": 2,
        "status": "success",
        "operation_key": "stage:missing-recovery",
        "request_fingerprint": None,
        "execution_id": None,
        "reservation_id": "missing-receipt",
        "result_state": "staged",
        "staged_payload": staged.payload,
        "staged_payload_sha256": staged.sha256,
        "staged_payload_bytes": staged.byte_count,
        "staged_job_count": staged.job_count,
    }
    monkeypatch.setattr(
        "app.services.firecrawl_result_staging._operation_row",
        lambda _db, _key: row,
    )
    recovered = recover_staged_firecrawl_result(
        db_session, operation_key="stage:missing-recovery", execution_id=None,
    )
    assert recovered.found is True
    assert recovered.blocked is True


def test_reconciliation_race_after_reservation_lock_preserves_staged_result(
    db_session, monkeypatch,
):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "staging-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    run_id = "stage-reconcile-race"
    owner_token = "stage-reconcile-owner"
    operation_key = "discovery:stage-reconcile-race"
    now = datetime.now(timezone.utc)
    db_session.add(FirecrawlDiscoveryRunModel(
        run_id=run_id,
        plan_hash="f" * 64,
        status="running",
        owner_token=owner_token,
        owner_expires_at=now + timedelta(seconds=1),
        started_at=now,
    ))
    execution = SearchExecutionModel(
        candidate_id="stage-reconcile-execution",
        cycle_id=run_id,
        status="started",
        provider_name="firecrawl",
        query_variant="Python",
        retrieval_location="Bengaluru",
        ownership_token=owner_token,
        ownership_expires_at=now + timedelta(seconds=1),
        selected_at=now,
        started_at=now,
    )
    db_session.add(execution)
    db_session.commit()
    receipt = reserve_provider_request(
        db_session,
        "firecrawl",
        cost_units=2,
        execution_id=execution.id,
        operation_key=operation_key,
    )
    assert receipt is not None
    assert mark_provider_request_in_flight(
        receipt,
        db=db_session,
        discovery_run_id=run_id,
        discovery_owner_token=owner_token,
    ) is not None

    import app.services.firecrawl_result_staging as staging_module
    original_validate = staging_module._validate_reservation
    validation_entered = Event()
    release_stage = Event()

    def hold_after_lock(*args, **kwargs):
        row = original_validate(*args, **kwargs)
        validation_entered.set()
        assert release_stage.wait(timeout=10)
        return row

    monkeypatch.setattr(staging_module, "_validate_reservation", hold_after_lock)
    stage_error: list[BaseException] = []

    def stage_worker():
        try:
            with SessionLocal() as session:
                stage_firecrawl_result(
                    session,
                    operation_key=operation_key,
                    operation="discovery",
                    cost_units=2,
                    execution_id=execution.id,
                    reservation_id=receipt.reservation_id,
                    jobs=[_job()],
                    discovery_run_id=run_id,
                    discovery_owner_token=owner_token,
                )
        except BaseException as exc:
            stage_error.append(exc)

    stage_thread = Thread(target=stage_worker)
    stage_thread.start()
    assert validation_entered.wait(timeout=10)

    def reconcile_worker():
        with SessionLocal() as session:
            reconcile_stale_discovery_reservations(session, run_id)

    reconcile_thread = Thread(target=reconcile_worker)
    reconcile_thread.start()
    time.sleep(1.2)
    release_stage.set()
    stage_thread.join(timeout=10)
    reconcile_thread.join(timeout=10)
    assert not stage_error
    assert not stage_thread.is_alive()
    assert not reconcile_thread.is_alive()

    assert db_session.execute(text(
        "SELECT result_state FROM firecrawl_operations WHERE operation_key=:key"
    ), {"key": operation_key}).scalar_one() == "staged"
    assert db_session.execute(text(
        "SELECT status FROM provider_reservations WHERE id=:id"
    ), {"id": receipt.reservation_id}).scalar_one() == "in_flight"

    db_session.execute(text(
        "UPDATE firecrawl_discovery_runs SET owner_expires_at=clock_timestamp()+INTERVAL '5 minutes' "
        "WHERE run_id=:run_id"
    ), {"run_id": run_id})
    db_session.execute(text(
        "UPDATE search_execution SET ownership_expires_at=clock_timestamp()+INTERVAL '5 minutes' "
        "WHERE id=:execution_id"
    ), {"execution_id": execution.id})
    db_session.commit()
    recovered = recover_staged_firecrawl_result(
        db_session,
        operation_key=operation_key,
        execution_id=execution.id,
        discovery_run_id=run_id,
        discovery_owner_token=owner_token,
        ownership_check=lambda: assert_discovery_ownership(
            db_session,
            run_id=run_id,
            owner_token=owner_token,
            execution_id=execution.id,
        ),
    )
    assert recovered.blocked is False
    assert db_session.execute(text(
        "SELECT result_state FROM firecrawl_operations WHERE operation_key=:key"
    ), {"key": operation_key}).scalar_one() == "consumed"
