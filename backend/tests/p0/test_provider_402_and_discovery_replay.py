"""P0 regressions for paid 402 handling and durable discovery replay."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.providers.exceptions import ProviderHTTPError
from app.providers.firecrawl import FirecrawlProvider
from app.main import app
from app.schemas.job_search import JobSearchQuery
from app.services.firecrawl_discovery import (
    DiscoveryPlanConflict,
    DiscoveryQuery,
    FirecrawlDiscoveryPlan,
    execute_discovery_run,
)
from app.services.ingestion import PaymentRequired, run_ingestion
from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError


def _configure_firecrawl(monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "p0-test-key")
    monkeypatch.setattr(settings, "firecrawl_discovery_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 100)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 100)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)


def _plan() -> FirecrawlDiscoveryPlan:
    return FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=1,
        max_results_per_query=10,
        max_credits_per_run=10,
        queries=[DiscoveryQuery(keywords="P0 Engineer", location="Bengaluru", page_size=10)],
    )


def test_search_402_preserves_provider_http_metadata():
    client = MagicMock(spec=FirecrawlClient)
    client.bounded_search_limit.side_effect = FirecrawlClient.bounded_search_limit
    client.search.side_effect = FirecrawlError("Payment Required", status_code=402)
    provider = FirecrawlProvider(client=client)

    with pytest.raises(ProviderHTTPError) as exc_info:
        provider.search_jobs(JobSearchQuery(keywords="engineer", location="Bengaluru"))

    assert exc_info.value.status_code == 402
    assert exc_info.value.billing_ambiguous is False


def test_search_402_through_ingestion_settles_paid_receipt(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    with respx.mock:
        route = respx.post("https://api.firecrawl.dev/v2/search").respond(
            402, json={"success": False, "error": "Payment Required"}
        )
        with pytest.raises(PaymentRequired):
            run_ingestion(
                db_session,
                "firecrawl",
                FirecrawlProvider(),
                JobSearchQuery(keywords="engineer", location="Bengaluru", page_size=10),
            )
        assert route.call_count == 1

    reservation = db_session.execute(
        text("SELECT status, cost_units FROM provider_reservations ORDER BY created_at DESC LIMIT 1"),
    ).one()
    assert reservation == ("payment_required", 2)
    assert db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl'"),
    ).scalar_one() == 2
    assert db_session.execute(text("SELECT payment_required FROM firecrawl_budget_state")).scalar_one() is True


def test_firecrawl_ingestion_endpoint_preserves_payment_required_instead_of_500(db_session, monkeypatch):
    _configure_firecrawl(monkeypatch)
    with respx.mock:
        route = respx.post("https://api.firecrawl.dev/v2/search").respond(
            402, json={"success": False, "error": "Payment Required"}
        )
        with TestClient(app) as client:
            response = client.post(
                "/ingestion/firecrawl", headers={"X-Api-Key": settings.api_secret_key},
                json={"keywords": "engineer", "location": "Bengaluru", "page_size": 10},
            )
        assert route.call_count == 1
    assert response.status_code == 402, response.text
    assert db_session.execute(text("SELECT status FROM provider_reservations")).scalar_one() == "payment_required"


@pytest.mark.parametrize("check", ["status", "credit_telemetry"])
def test_search_402_through_discovery_is_latched_and_replayed_without_provider_call(
    db_session, monkeypatch, check
):
    _configure_firecrawl(monkeypatch)
    provider = MagicMock()
    provider.search_jobs.side_effect = ProviderHTTPError("Payment Required", status_code=402)
    provider.last_search_telemetry = {}

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider):
        first = execute_discovery_run(db_session, plan=_plan(), run_id="p0-402-discovery", force=True)
        second = execute_discovery_run(db_session, plan=_plan(), run_id="p0-402-discovery", force=True)

    if check == "status":
        assert first.status == "payment_required"
    else:
        assert first.credits_consumed == first.credits_reserved == 2
    assert second.model_dump() == first.model_dump()
    assert provider.search_jobs.call_count == 1
    assert db_session.execute(
        text("SELECT status FROM firecrawl_discovery_runs WHERE run_id = 'p0-402-discovery'"),
    ).scalar_one() == first.status


def test_discovery_worker_crash_is_persisted_as_unknown_and_not_retried(db_session):
    plan = _plan()
    with patch(
        "app.services.firecrawl_discovery._execute_discovery_run_body",
        side_effect=RuntimeError("worker crashed after dispatch"),
    ):
        with pytest.raises(RuntimeError, match="worker crashed"):
            execute_discovery_run(db_session, plan=plan, run_id="p0-crash-replay", force=True)

    assert db_session.execute(
        text("SELECT status FROM firecrawl_discovery_runs WHERE run_id = 'p0-crash-replay'"),
    ).scalar_one() == "unknown"

    with patch("app.services.firecrawl_discovery._execute_discovery_run_body") as body:
        replay = execute_discovery_run(db_session, plan=plan, run_id="p0-crash-replay", force=True)

    assert replay.status == "unknown"
    body.assert_not_called()


def test_same_run_real_receipt_is_charged_once_concurrently_sequentially_and_on_plan_conflict(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    entered = Event()
    release = Event()
    provider = MagicMock()
    provider.last_search_telemetry = {}

    def search(_query):
        entered.set()
        assert release.wait(timeout=15), "Test did not release provider boundary"
        return []

    provider.search_jobs.side_effect = search

    def execute():
        with Session(db_session.get_bind()) as session:
            return execute_discovery_run(session, plan=_plan(), run_id="p0-race-receipt", force=True)

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(execute)
            try:
                assert entered.wait(timeout=15), "First worker never entered provider call"
                concurrent = pool.submit(execute).result(timeout=15)
                assert concurrent.status == "running"
            finally:
                release.set()
            completed = first_future.result(timeout=15)
        sequential = execute()
        conflicting = _plan()
        conflicting.queries[0].keywords = "Changed plan engineer"
        with pytest.raises(DiscoveryPlanConflict):
            execute_discovery_run(db_session, plan=conflicting, run_id="p0-race-receipt", force=True)

    assert completed.status == "completed"
    assert sequential.model_dump() == completed.model_dump()
    provider.search_jobs.assert_called_once()
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 1
    assert db_session.execute(text("SELECT status, cost_units FROM provider_reservations")).one() == ("succeeded", 2)
    assert db_session.execute(
        text("SELECT SUM(request_count) FROM provider_usage WHERE provider_name='firecrawl'")
    ).scalar_one() == 2


def test_duplicate_dispatch_cancels_only_reserved_receipt_and_preserves_worker_ledger(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    entered = Event()
    release = Event()
    provider = MagicMock()

    def search(_query):
        entered.set()
        assert release.wait(timeout=15), "Test did not release provider boundary"
        return []

    provider.search_jobs.side_effect = search
    query = JobSearchQuery(keywords="P0 duplicate", location="Bengaluru", page_size=10)

    def execute():
        with Session(db_session.get_bind()) as session:
            return run_ingestion(session, "firecrawl", provider, query)

    # With no execution id, the generated operation key is the only shared
    # identity.  This models two workers arriving at dispatch concurrently.
    with patch("app.services.ingestion.uuid.uuid4", return_value="p0-duplicate-dispatch"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(execute)
            assert entered.wait(timeout=15), "First worker never entered provider call"
            duplicate = pool.submit(execute).result(timeout=15)
            release.set()
            first = first_future.result(timeout=15)

    assert duplicate[0].failed == 1
    assert first[0].failed == 0
    provider.search_jobs.assert_called_once()
    assert db_session.execute(
        text("SELECT status FROM provider_reservations")
    ).scalar_one() == "succeeded"
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_reservations")
    ).scalar_one() == 1
    operation = db_session.execute(
        text("SELECT status, metadata_json FROM firecrawl_operations")
    ).one()
    assert operation[0] == "success"
    assert operation[1] == {"result_count": 0}


def test_process_death_mid_provider_leaves_accounted_inflight_and_stale_run_does_not_retry(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    provider = MagicMock()
    provider.search_jobs.side_effect = SystemExit("simulated process death")

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider):
        with pytest.raises(SystemExit, match="process death"):
            execute_discovery_run(db_session, plan=_plan(), run_id="p0-dead-worker", force=True)
        assert db_session.execute(text("SELECT status FROM provider_reservations")).scalar_one() == "in_flight"
        db_session.execute(
            text(
                "UPDATE firecrawl_discovery_runs SET "
                "started_at=clock_timestamp()-INTERVAL '1 hour', "
                "owner_expires_at=clock_timestamp()-INTERVAL '1 hour' "
                "WHERE run_id='p0-dead-worker'"
            )
        )
        db_session.execute(
            text(
                "UPDATE search_execution SET ownership_expires_at=clock_timestamp()-INTERVAL '1 hour' "
                "WHERE cycle_id='p0-dead-worker'"
            )
        )
        db_session.commit()
        replay = execute_discovery_run(db_session, plan=_plan(), run_id="p0-dead-worker", force=True)
        second_replay = execute_discovery_run(db_session, plan=_plan(), run_id="p0-dead-worker", force=True)

    provider.search_jobs.assert_called_once()
    assert replay.status == "unknown"
    assert second_replay.model_dump() == replay.model_dump()
    assert db_session.execute(
        text("SELECT SUM(request_count) FROM provider_usage WHERE provider_name='firecrawl'")
    ).scalar_one() == 2
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_reservations")).scalar_one() == 1
    assert db_session.execute(text("SELECT status FROM provider_reservations")).scalar_one() == "unknown_after_crash"
    assert db_session.execute(text("SELECT status FROM firecrawl_operations")).scalar_one() == "unknown_after_crash"
    assert replay.execution_ids
    assert replay.credits_consumed == replay.credits_reserved == 2


def test_settlement_failure_after_provider_result_is_unknown_and_replay_is_durable(
    db_session, monkeypatch
):
    _configure_firecrawl(monkeypatch)
    provider = MagicMock()
    provider.search_jobs.return_value = []
    provider.last_search_telemetry = {}
    from app.services.firecrawl_quota import settle_provider_reservation as real_settle
    settlement_calls = 0

    def fail_after_dispatch(*args, **kwargs):
        nonlocal settlement_calls
        settlement_calls += 1
        if settlement_calls == 1:
            return real_settle(*args, **kwargs)
        raise RuntimeError("settlement connection lost")

    with patch("app.services.firecrawl_discovery.create_provider", return_value=provider), patch(
        "app.services.firecrawl_quota.settle_provider_reservation",
        side_effect=fail_after_dispatch,
    ):
        first = execute_discovery_run(db_session, plan=_plan(), run_id="p0-settlement-failure", force=True)
        db_session.execute(
            text(
                "UPDATE firecrawl_discovery_runs "
                "SET started_at=clock_timestamp()-INTERVAL '1 hour' "
                "WHERE run_id='p0-settlement-failure'"
            )
        )
        db_session.commit()
    second = execute_discovery_run(db_session, plan=_plan(), run_id="p0-settlement-failure", force=True)

    assert first.status == "unknown"
    assert second.model_dump() == first.model_dump()
    provider.search_jobs.assert_called_once()
    assert db_session.execute(
        text("SELECT status FROM provider_reservations")
    ).scalar_one() == "succeeded"
    assert db_session.execute(
        text("SELECT status, result_state FROM firecrawl_operations")
    ).one() == ("success", "consumed")
    assert db_session.execute(
        text(
            "SELECT r.status, r.execution_id, r.operation_key, o.reservation_id, "
            "o.execution_id, o.operation_key "
            "FROM provider_reservations r "
            "JOIN firecrawl_operations o ON o.operation_key=r.operation_key"
        )
    ).one() == (
        "succeeded",
        first.execution_ids[0],
        f"discovery:firecrawl:{first.execution_ids[0]}",
        db_session.execute(text("SELECT id FROM provider_reservations")).scalar_one(),
        first.execution_ids[0],
        f"discovery:firecrawl:{first.execution_ids[0]}",
    )
    assert db_session.execute(
        text("SELECT status FROM search_execution WHERE id = :id"),
        {"id": first.execution_ids[0]},
    ).scalar_one() == "succeeded"
