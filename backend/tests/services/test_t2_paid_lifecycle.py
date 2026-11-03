"""Focused unit coverage for the T2 ownership/billing primitives."""

import concurrent.futures
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.db.database import SessionLocal
from app.providers.exceptions import ProviderHTTPError, ProviderNetworkError
from app.services.enrichment_worker import EnrichmentWorker
from app.services.firecrawl_quota import (
    ProviderReservationReceipt,
    mark_provider_request_in_flight,
)
from app.services.scraper.firecrawl import FirecrawlClient
from app.services.ingestion import run_ingestion, reserve_provider_request
from app.schemas.job_search import JobSearchQuery


def test_provider_receipt_is_idempotency_addressable():
    receipt = ProviderReservationReceipt(
        reservation_id="r-1",
        operation_key="enrichment:12:lease",
        provider_name="firecrawl_monthly",
        cost_units=1,
    )
    assert receipt.id == "r-1"
    assert receipt["operation_key"] == "enrichment:12:lease"
    assert receipt.get("status") == "reserved"


def test_scrape_contract_and_search_bound_are_fixed_cost_contracts():
    assert FirecrawlClient.scrape_billing_contract_verified() is True
    assert FirecrawlClient.bounded_search_limit(10) == 10
    assert FirecrawlClient.bounded_search_limit(1000) == FirecrawlClient.MAX_RESULTS


def test_provider_http_metadata_and_ambiguous_transport_are_preserved():
    payment = ProviderHTTPError("payment required", status_code=402)
    transport = ProviderNetworkError("connection reset")
    assert payment.status_code == 402
    assert payment.billing_ambiguous is False
    assert transport.billing_ambiguous is True


def test_terminal_transition_clears_lease_fields_and_is_fenced():
    db = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = (12,)
    db.execute.return_value = result

    transitioned = EnrichmentWorker().fence_terminal_transition(
        db,
        12,
        "lease-token",
        "failure",
        error_reason="unknown_after_crash",
    )

    assert transitioned is True
    sql = str(db.execute.call_args.args[0])
    assert "lease_holder = NULL" in sql
    assert "lease_token = NULL" in sql
    assert "lease_expires_at = NULL" in sql
    assert "lease_token = :token" in sql


def test_expired_lease_probe_precedes_native_or_paid_calls():
    db = MagicMock()
    db.execute.return_value.scalar.return_value = False
    worker = EnrichmentWorker()
    worker.ssrf_client.fetch = MagicMock()
    worker._reserve_firecrawl_receipt = MagicMock()

    worker.process_job(db, 77, "https://example.invalid/job", "expired-token")

    worker.ssrf_client.fetch.assert_not_called()
    worker._reserve_firecrawl_receipt.assert_not_called()


def test_unowned_direct_reservations_use_unique_keys_and_obey_cap(db_session):
    """Legacy direct callers must not reuse one ``None`` operation key."""
    original = (
        settings.firecrawl_api_key,
        settings.firecrawl_enabled,
        settings.firecrawl_monthly_budget,
        settings.firecrawl_reserved_credits,
        settings.firecrawl_monthly_automation_cap,
    )
    try:
        settings.firecrawl_api_key = "t2-test-key"
        settings.firecrawl_enabled = True
        settings.firecrawl_monthly_budget = 5
        settings.firecrawl_reserved_credits = 0
        settings.firecrawl_monthly_automation_cap = 900
        for table in ("firecrawl_operations", "provider_reservations", "firecrawl_budget_state"):
            db_session.execute(text(f"DELETE FROM {table}"))
        db_session.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
        db_session.commit()

        def attempt() -> bool:
            db = SessionLocal()
            try:
                return EnrichmentWorker()._reserve_firecrawl_credit(db)
            finally:
                db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: attempt(), range(10)))

        assert sum(results) == 5
        rows = db_session.execute(text(
            "SELECT operation_key FROM provider_reservations WHERE provider_name='firecrawl_monthly'"
        )).scalars().all()
        assert len(rows) == 5
        assert len(set(rows)) == 5
    finally:
        (
            settings.firecrawl_api_key,
            settings.firecrawl_enabled,
            settings.firecrawl_monthly_budget,
            settings.firecrawl_reserved_credits,
            settings.firecrawl_monthly_automation_cap,
        ) = original


def test_stale_direct_reservation_is_unknown_and_same_key_cannot_replay(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "direct-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 20)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 20)

    key = "direct:process-death"
    first_provider = MagicMock()
    first_provider.search_jobs.side_effect = SystemExit("simulated process death after dispatch")
    with pytest.raises(SystemExit, match="process death"):
        run_ingestion(
            db_session,
            "firecrawl",
            first_provider,
            JobSearchQuery(keywords="Python", location="Bengaluru", page=1, page_size=10),
            operation_key=key,
        )

    reservation = db_session.execute(
        text("SELECT id, status FROM provider_reservations WHERE operation_key=:key"),
        {"key": key},
    ).one()
    assert reservation.status == "in_flight"
    db_session.execute(
        text("UPDATE provider_reservations SET created_at=clock_timestamp()-INTERVAL '1 hour' WHERE id=:id"),
        {"id": reservation.id},
    )
    db_session.commit()

    reservation_count = db_session.execute(
        text("SELECT COUNT(*) FROM provider_reservations WHERE operation_key=:key"),
        {"key": key},
    ).scalar_one()
    usage_count = db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name='firecrawl'"),
    ).scalar_one()

    provider = MagicMock()
    provider.search_jobs.return_value = []
    result, _ = run_ingestion(
        db_session,
        "firecrawl",
        provider,
        JobSearchQuery(keywords="Python", location="Bengaluru", page=1, page_size=10),
        operation_key=key,
    )
    assert result.failed == 1
    provider.search_jobs.assert_not_called()
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_reservations WHERE operation_key=:key"),
        {"key": key},
    ).scalar_one() == reservation_count
    assert db_session.execute(
        text("SELECT request_count FROM provider_usage WHERE provider_name='firecrawl'"),
    ).scalar_one() == usage_count
    assert db_session.execute(
        text("SELECT status, execution_id, job_id, operation_key FROM provider_reservations WHERE id=:id"),
        {"id": reservation.id},
    ).one() == ("unknown_after_crash", None, None, key)
    assert db_session.execute(
        text("SELECT status, reservation_id, operation_key FROM firecrawl_operations WHERE operation_key=:key"),
        {"key": key},
    ).one() == ("unknown_after_crash", reservation.id, key)


def test_fresh_direct_in_flight_reservation_is_not_reconciled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_enabled", True)
    monkeypatch.setattr(settings, "firecrawl_api_key", "direct-test-key")
    monkeypatch.setattr(settings, "firecrawl_monthly_budget", 20)
    monkeypatch.setattr(settings, "firecrawl_reserved_credits", 0)
    monkeypatch.setattr(settings, "firecrawl_monthly_automation_cap", 20)

    key = "direct:fresh-process-death"
    provider = MagicMock()
    provider.search_jobs.side_effect = SystemExit("simulated process death after dispatch")
    with pytest.raises(SystemExit, match="process death"):
        run_ingestion(
            db_session,
            "firecrawl",
            provider,
            JobSearchQuery(keywords="Python", location="Bengaluru", page=1, page_size=10),
            operation_key=key,
        )

    result, _ = run_ingestion(
        db_session,
        "firecrawl",
        provider,
        JobSearchQuery(keywords="Python", location="Bengaluru", page=1, page_size=10),
        operation_key=key,
    )
    assert result.failed == 1
    provider.search_jobs.assert_called_once()
    assert db_session.execute(
        text("SELECT status FROM provider_reservations WHERE operation_key=:key"),
        {"key": key},
    ).scalar_one() == "in_flight"
    assert db_session.execute(
        text("SELECT status FROM firecrawl_operations WHERE operation_key=:key"),
        {"key": key},
    ).scalar_one() == "in_flight"
