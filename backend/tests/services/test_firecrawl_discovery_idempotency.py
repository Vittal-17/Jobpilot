"""Focused ownership/idempotency tests for the discovery orchestration fence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models.execution_outcome import FirecrawlDiscoveryRunModel
from app.db.models.search_execution import SearchExecutionModel
from app.models.job import Job
from app.services.firecrawl_discovery import (
    DiscoveryPlanConflict,
    DiscoveryQuery,
    FirecrawlDiscoveryPlan,
    _mark_discovery_unknown,
    _plan_hash,
    execute_discovery_run,
)
from app.services.firecrawl_quota import mark_provider_request_in_flight
from app.services.ingestion import reserve_provider_request
from app.schemas.job_search import IngestionResult


@pytest.fixture(autouse=True)
def discovery_settings():
    old = {
        "enabled": settings.firecrawl_discovery_enabled,
        "api_key": settings.firecrawl_api_key,
        "firecrawl": settings.firecrawl_enabled,
        "reserve": settings.firecrawl_reserved_credits,
    }
    settings.firecrawl_discovery_enabled = True
    settings.firecrawl_api_key = "test-discovery-key"
    settings.firecrawl_enabled = True
    settings.firecrawl_reserved_credits = 0
    try:
        yield
    finally:
        settings.firecrawl_discovery_enabled = old["enabled"]
        settings.firecrawl_api_key = old["api_key"]
        settings.firecrawl_enabled = old["firecrawl"]
        settings.firecrawl_reserved_credits = old["reserve"]
        with SessionLocal() as db:
            for table in ("firecrawl_operations", "provider_reservations", "firecrawl_discovery_runs", "firecrawl_query_state"):
                db.execute(text(f"DELETE FROM {table}"))
            db.commit()


def _plan(keyword="Python Engineer"):
    return FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=1,
        max_results_per_query=10,
        max_credits_per_run=10,
        queries=[DiscoveryQuery(keywords=keyword, location="Bengaluru", page_size=10)],
    )


def _two_query_plan():
    return FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=2,
        max_results_per_query=10,
        max_credits_per_run=20,
        queries=[
            DiscoveryQuery(keywords="Python Engineer", location="Bengaluru", page_size=10),
            DiscoveryQuery(keywords="Django Engineer", location="Bengaluru", page_size=10),
        ],
    )


def test_same_run_sequential_replays_stored_receipt_and_does_not_call_provider(db_session):
    provider = MagicMock()
    provider.search_jobs.return_value = []
    provider.last_search_telemetry = {}
    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider), patch(
        "app.services.firecrawl_discovery.run_ingestion",
        return_value=(IngestionResult(provider="firecrawl", fetched=0), []),
    ) as ingest:
        with SessionLocal() as db:
            first = execute_discovery_run(db, plan=_plan(), run_id="stable-sequential-run", force=True)
            second = execute_discovery_run(db, plan=_plan(), run_id="stable-sequential-run", force=True)

    assert first.status == "completed"
    assert second.model_dump() == first.model_dump()
    assert ingest.call_count == 1
    assert provider.search_jobs.call_count == 0  # run_ingestion is the provider boundary in this test
    with SessionLocal() as db:
        operation_key = db.execute(
            text("SELECT operation_key FROM firecrawl_operations WHERE execution_id = :eid"),
            {"eid": first.execution_ids[0]},
        ).scalar_one()
    assert operation_key == f"discovery:firecrawl:{first.execution_ids[0]}"


def test_same_run_plan_hash_conflict_is_fail_closed(db_session):
    with patch("app.services.firecrawl_discovery.create_provider"):
        with SessionLocal() as db:
            execute_discovery_run(db, plan=_plan(), run_id="stable-conflict-run", force=True)
            with pytest.raises(DiscoveryPlanConflict):
                execute_discovery_run(db, plan=_plan("Django Engineer"), run_id="stable-conflict-run", force=True)


def test_same_run_concurrent_claim_allows_at_most_one_worker(db_session):
    provider = MagicMock()
    provider.search_jobs.return_value = []
    provider.last_search_telemetry = {}

    def worker():
        with SessionLocal() as db:
            return execute_discovery_run(db, plan=_plan(), run_id="stable-concurrent-run", force=True)

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider), patch(
        "app.services.firecrawl_discovery.run_ingestion",
        return_value=(IngestionResult(provider="firecrawl", fetched=0), []),
    ) as ingest:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(worker), pool.submit(worker))]

    assert ingest.call_count == 1
    assert {result.status for result in results} <= {"completed", "running"}


def test_concurrent_two_query_duplicate_cannot_recover_partial_run_or_complete_it(db_session):
    provider = MagicMock()
    provider.last_search_telemetry = {}
    entered = Event()
    release = Event()
    calls = 0

    def search(_query):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(timeout=15)
        return []

    provider.search_jobs.side_effect = search

    def worker():
        with SessionLocal() as db:
            return execute_discovery_run(db, plan=_two_query_plan(), run_id="two-query-active-race", force=True)

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(worker)
            assert entered.wait(timeout=15)
            duplicate = pool.submit(worker).result(timeout=15)
            assert duplicate.status == "running"
            release.set()
            completed = first_future.result(timeout=15)

    assert completed.status == "completed"
    assert completed.attempted_queries == 2
    assert calls == 2
    with SessionLocal() as db:
        assert db.execute(text(
            "SELECT COUNT(*) FROM search_execution WHERE cycle_id='two-query-active-race'"
        )).scalar_one() == 2


def test_stale_partial_run_recovers_staged_query_and_continues_unstarted_query(db_session):
    plan = _two_query_plan()
    provider = MagicMock()
    provider.search_jobs.return_value = []
    provider.last_search_telemetry = {}
    from app.services.ingestion import run_ingestion as real_run_ingestion
    calls = 0

    def crash_before_second_dispatch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SystemExit("crash before second dispatch")
        return real_run_ingestion(*args, **kwargs)

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider), patch(
        "app.services.firecrawl_discovery.run_ingestion",
        side_effect=crash_before_second_dispatch,
    ):
        with pytest.raises(SystemExit, match="before second dispatch"):
            execute_discovery_run(db_session, plan=plan, run_id="stale-partial-run", force=True)

    db_session.execute(text(
        "UPDATE firecrawl_discovery_runs "
        "SET started_at=clock_timestamp()-INTERVAL '1 hour', "
        "owner_expires_at=clock_timestamp()-INTERVAL '1 hour' "
        "WHERE run_id='stale-partial-run'"
    ))
    db_session.execute(text(
        "UPDATE search_execution SET ownership_expires_at=clock_timestamp()-INTERVAL '1 hour' "
        "WHERE cycle_id='stale-partial-run' AND status IN ('selected', 'started')"
    ))
    expired_owner = db_session.execute(text(
        "SELECT owner_token FROM firecrawl_discovery_runs WHERE run_id='stale-partial-run'"
    )).scalar_one()
    db_session.commit()

    _mark_discovery_unknown(
        db_session,
        "stale-partial-run",
        "expired worker attempted publication",
        expired_owner,
    )
    assert db_session.execute(text(
        "SELECT status FROM firecrawl_discovery_runs WHERE run_id='stale-partial-run'"
    )).scalar_one() == "running"

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider):
        recovered = execute_discovery_run(
            db_session, plan=plan, run_id="stale-partial-run", force=True,
        )

    assert recovered.status == "completed"
    assert recovered.attempted_queries == 2
    assert calls == 2
    assert provider.search_jobs.call_count == 2
    assert db_session.execute(text(
        "SELECT COUNT(*) FROM search_execution WHERE cycle_id='stale-partial-run' AND status='succeeded'"
    )).scalar_one() == 2


def test_stale_parent_cannot_invalidate_live_child_receipt_or_staged_result(db_session):
    plan = _two_query_plan()
    provider = MagicMock()
    provider.last_search_telemetry = {}
    staged_job = Job(
        title="Lease Engineer",
        company="Lease Corp",
        source="firecrawl",
        source_job_id="lease-staged-job",
        location="Bengaluru",
        discovered_at=datetime.now(timezone.utc),
    )
    provider.search_jobs.side_effect = [[], SystemExit("crash with second receipt in flight")]
    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider):
        # Query one has durable content and query two dies after its
        # in-flight receipt is established.
        provider.search_jobs.side_effect = [
            [staged_job],
            SystemExit("crash with second receipt in flight"),
        ]
        with pytest.raises(SystemExit, match="second receipt"):
            execute_discovery_run(db_session, plan=plan, run_id="live-child-fence", force=True)

    db_session.execute(text(
        "UPDATE firecrawl_discovery_runs "
        "SET owner_expires_at=clock_timestamp()-INTERVAL '1 second' "
        "WHERE run_id='live-child-fence'"
    ))
    db_session.commit()

    recovered = execute_discovery_run(db_session, plan=plan, run_id="live-child-fence", force=True)

    assert recovered.status == "running"
    assert provider.search_jobs.call_count == 2
    assert db_session.execute(text(
        "SELECT result_state, staged_job_count FROM firecrawl_operations "
        "WHERE operation_key=(SELECT operation_key FROM provider_reservations "
        "WHERE execution_id=(SELECT id FROM search_execution WHERE cycle_id='live-child-fence' ORDER BY id LIMIT 1))"
    )).one() == ("consumed", 1)
    assert db_session.execute(text(
        "SELECT status FROM provider_reservations WHERE execution_id="
        "(SELECT id FROM search_execution WHERE cycle_id='live-child-fence' ORDER BY id DESC LIMIT 1)"
    )).scalar_one() == "in_flight"


def test_stale_running_run_replays_unknown_without_automatic_retry(db_session):
    plan = _plan()
    with SessionLocal() as db:
        db.add(
            FirecrawlDiscoveryRunModel(
                run_id="stale-crashed-run",
                plan_hash=_plan_hash(plan),
                status="running",
                started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        db.commit()
        with patch("app.services.firecrawl_discovery.create_provider") as provider:
            result = execute_discovery_run(db, plan=plan, run_id="stale-crashed-run", force=True)

    assert result.status == "unknown"
    provider.assert_not_called()
    assert "external outcome is unknown" in (result.reason or "").lower()


def test_unknown_parent_reconciles_receipts_and_refreshes_durable_unknown_receipt(db_session):
    plan = _plan()
    run_id = "unknown-parent-authoritative"
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(FirecrawlDiscoveryRunModel(
            run_id=run_id,
            plan_hash=_plan_hash(plan),
            status="unknown",
            receipt={"run_id": run_id, "status": "unknown", "execution_ids": []},
            started_at=now,
        ))
        execution = SearchExecutionModel(
            candidate_id="unknown-parent-execution",
            cycle_id=run_id,
            status="started",
            provider_name="firecrawl",
            selected_at=now,
            started_at=now,
        )
        db.add(execution)
        db.commit()
        receipt = reserve_provider_request(
            db,
            "firecrawl",
            cost_units=2,
            execution_id=execution.id,
            operation_key="discovery:unknown-parent",
        )
        assert receipt is not None
        assert mark_provider_request_in_flight(receipt, db=db) is not None
        db.execute(
            text("UPDATE search_execution SET status='failed' WHERE id=:id"),
            {"id": execution.id},
        )
        db.commit()

        with patch("app.services.firecrawl_discovery.create_provider") as provider:
            result = execute_discovery_run(db, plan=plan, run_id=run_id, force=True)

        provider.assert_not_called()
        assert result.status == "unknown"
        assert result.execution_ids == [execution.id]
        assert result.credits_consumed == 2
        assert db.execute(
            text("SELECT status FROM provider_reservations WHERE id=:id"),
            {"id": receipt.id},
        ).scalar_one() == "unknown_after_crash"
        assert db.execute(
            text("SELECT status, reservation_id, execution_id FROM firecrawl_operations WHERE operation_key=:key"),
            {"key": receipt.operation_key},
        ).one() == ("unknown_after_crash", receipt.id, execution.id)
