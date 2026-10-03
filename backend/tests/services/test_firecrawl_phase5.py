import pytest
import concurrent.futures
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
import yaml
import re
import httpx
import uuid
import time

from app.core.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.firecrawl_operation import FirecrawlOperationModel
from app.services.enrichment_worker import EnrichmentWorker
from app.services.firecrawl_quota import (
    calculate_search_credits,
    get_firecrawl_budget_state,
    record_firecrawl_operation,
)
from app.services.ingestion import acquire_provider_request_slot
from app.services.scraper.firecrawl import FirecrawlError

client = TestClient(app)

def clear_test_db(db):
    db.execute(text("DELETE FROM firecrawl_operations"))
    db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly', 'firecrawl_quota_denied')"))
    db.execute(text("DELETE FROM job_enrichments WHERE job_id IN (SELECT id FROM jobs WHERE source = 'test_phase5')"))
    db.execute(text("DELETE FROM jobs WHERE source = 'test_phase5'"))
    db.commit()

@pytest.fixture(autouse=True)
def clean_environment():
    orig_key = settings.firecrawl_api_key
    orig_enabled = settings.firecrawl_enabled
    orig_budget = settings.firecrawl_monthly_budget
    orig_cap = settings.firecrawl_monthly_automation_cap
    orig_reserve = settings.firecrawl_reserved_credits
    orig_discovery_enabled = settings.firecrawl_discovery_enabled
    orig_enrich_cap = settings.firecrawl_enrichment_max_credits_per_run

    settings.firecrawl_api_key = "test_firecrawl_key"
    settings.firecrawl_enabled = True
    settings.firecrawl_monthly_budget = 1000
    settings.firecrawl_monthly_automation_cap = 900
    settings.firecrawl_reserved_credits = 100
    settings.firecrawl_discovery_enabled = True
    settings.firecrawl_enrichment_max_credits_per_run = 10

    with SessionLocal() as db:
        clear_test_db(db)

    yield

    with SessionLocal() as db:
        clear_test_db(db)

    settings.firecrawl_api_key = orig_key
    settings.firecrawl_enabled = orig_enabled
    settings.firecrawl_monthly_budget = orig_budget
    settings.firecrawl_monthly_automation_cap = orig_cap
    settings.firecrawl_reserved_credits = orig_reserve
    settings.firecrawl_discovery_enabled = orig_discovery_enabled
    settings.firecrawl_enrichment_max_credits_per_run = orig_enrich_cap


# 1. Model & Constraints
def test_firecrawl_operations_model_constraints():
    with SessionLocal() as db:
        # Valid insertion
        op = FirecrawlOperationModel(
            operation="discovery",
            cost_units=2,
            status="success"
        )
        db.add(op)
        db.commit()
        assert op.id is not None
        assert op.created_at is not None

        # Invalid operation constraint
        with pytest.raises(Exception):
            invalid_op = FirecrawlOperationModel(
                operation="unsupported_op",
                cost_units=1,
                status="success"
            )
            db.add(invalid_op)
            db.commit()
        db.rollback()

        # Invalid status constraint
        with pytest.raises(Exception):
            invalid_status = FirecrawlOperationModel(
                operation="enrichment",
                cost_units=1,
                status="unknown_status"
            )
            db.add(invalid_status)
            db.commit()
        db.rollback()

        # Invalid negative cost_units
        with pytest.raises(Exception):
            invalid_cost = FirecrawlOperationModel(
                operation="enrichment",
                cost_units=-5,
                status="success"
            )
            db.add(invalid_cost)
            db.commit()
        db.rollback()


# 2. Quota Denial Survives Rollback & provider_usage Exclusivity
def test_quota_denial_survives_rollback_without_increasing_provider_usage():
    with SessionLocal() as db:
        # Seed 900 credits in provider_usage for firecrawl (exact monthly ceiling)
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', CURRENT_DATE, 900)
        """))
        db.commit()

        # Attempt to acquire slot - should be denied
        res = acquire_provider_request_slot(db, "firecrawl", cost_units=2)
        assert res is False

        # provider_usage MUST strictly remain at 900
        actual_usage = db.execute(text("""
            SELECT request_count FROM provider_usage
            WHERE provider_name = 'firecrawl' AND usage_date = CURRENT_DATE
        """)).scalar()
        assert actual_usage == 900

        # provider_usage MUST NOT contain quota_denied records (exclusive credit ledger)
        denied_usage = db.execute(text("""
            SELECT COUNT(*) FROM provider_usage WHERE provider_name = 'firecrawl_quota_denied'
        """)).scalar()
        assert denied_usage == 0

        # firecrawl_operations MUST record the denial event
        denial_event = db.execute(text("""
            SELECT operation, cost_units, status FROM firecrawl_operations
            WHERE status = 'denied'
        """)).mappings().first()
        assert denial_event is not None
        assert denial_event["operation"] == "discovery"
        assert denial_event["cost_units"] == 2
        assert denial_event["status"] == "denied"

        # Check telemetry reports denial
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 900
        assert state["monthly_used"] == 900
        assert state["monthly_remaining"] == 0
        assert state["quota_denied"] == 1
        assert state["firecrawl_requests_denied_quota"] == 1


# 3. Enrichment Per-Run Cap Enforcement
def test_enrichment_per_run_cap_enforced():
    settings.firecrawl_enrichment_max_credits_per_run = 10
    worker = EnrichmentWorker(claim_batch_size=15)

    with SessionLocal() as db:
        # Create 12 jobs in db
        job_ids = []
        for i in range(12):
            job = JobModel(
                title=f"Software Engineer {i}",
                company="TechCorp",
                source="test_phase5",
                source_job_id=f"p5_cap_{i}",
                discovered_at=datetime.now(timezone.utc),
                description=f"Initial snippet {i}",
                description_is_snippet=True,
                url=f"https://example.com/jobs/{i}"
            )
            db.add(job)
            db.flush()
            job_ids.append(job.id)

            enrichment = JobEnrichmentModel(
                job_id=job.id,
                status="pending",
                url=job.url,
            )
            db.add(enrichment)
        db.commit()

        # Create mock HTTP 403 error for native fetch
        mock_req = httpx.Request("GET", "https://example.com")
        mock_resp = httpx.Response(403, request=mock_req)
        mock_http_err = httpx.HTTPStatusError("403", request=mock_req, response=mock_resp)

        # Patch FirecrawlClient.scrape to succeed
        with patch.object(worker.ssrf_client, "fetch", side_effect=mock_http_err):
            with patch("app.services.scraper.firecrawl.FirecrawlClient.scrape") as mock_scrape:
                mock_scrape.return_value = "Long extracted markdown description exceeding 200 chars for successful testing. " * 5

                # Process the claimed batch
                worker.claim_and_process(db)

                # Exactly 10 Firecrawl scrape calls should have executed
                assert mock_scrape.call_count == 10
                assert worker.credits_used_this_run == 10

        # Verify DB state: first 10 succeeded with Firecrawl, remaining 2 fell back to snippet
        enrichments = db.execute(text("""
            SELECT job_id, status, error_reason FROM job_enrichments
            WHERE job_id = ANY(:jids) ORDER BY job_id ASC
        """), {"jids": list(job_ids)}).mappings().all()

        success_count = sum(1 for e in enrichments if e["status"] == "success")
        unsupported_count = sum(1 for e in enrichments if e["status"] == "unsupported")

        assert success_count == 10
        assert unsupported_count == 2

        for e in enrichments:
            if e["status"] == "unsupported":
                assert "per-run credit cap reached" in (e["error_reason"] or "").lower()

        # provider_usage should have exactly 10 credits reserved
        fc_usage = db.execute(text("""
            SELECT request_count FROM provider_usage
            WHERE provider_name = 'firecrawl_monthly'
        """)).scalar()
        assert fc_usage == 10


# 4. Direct Fetch Success Causes Zero Firecrawl Calls and Credits
def test_direct_fetch_success_makes_zero_firecrawl_calls():
    worker = EnrichmentWorker()

    with SessionLocal() as db:
        job = JobModel(
            title="Native Engineer",
            company="NativeCorp",
            source="test_phase5",
            source_job_id="p5_native_1",
            discovered_at=datetime.now(timezone.utc),
            description="Native snippet",
            description_is_snippet=True,
            url="https://example.com/native"
        )
        db.add(job)
        db.flush()
        enrichment = JobEnrichmentModel(
            job_id=job.id,
            status="in_progress",
            lease_token="token_123",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            url=job.url
        )
        db.add(enrichment)
        db.commit()

        mock_resp = MagicMock()
        mock_resp.text = "<html><body>" + ("Direct fetch text with more than 200 characters to pass extraction. " * 5) + "</body></html>"
        mock_resp.raise_for_status.return_value = None

        with patch.object(worker.ssrf_client, "fetch", return_value=mock_resp):
            with patch("app.services.scraper.firecrawl.FirecrawlClient.scrape") as mock_scrape:
                worker.process_job(db, job.id, job.url, "token_123")
                assert mock_scrape.call_count == 0

        # Check job is updated to success
        updated_job = db.query(JobModel).filter(JobModel.id == job.id).first()
        assert updated_job.description_is_snippet is False
        assert "Direct fetch" in updated_job.description

        # Zero Firecrawl usage and zero operations
        usage = db.execute(text("SELECT COUNT(*) FROM provider_usage WHERE provider_name = 'firecrawl_monthly'")).scalar()
        assert usage == 0
        ops = db.execute(text("SELECT COUNT(*) FROM firecrawl_operations WHERE operation = 'enrichment'")).scalar()
        assert ops == 0


# 5. Firecrawl Failure and Payment-Required Behavior
def test_enrichment_firecrawl_failure_and_payment_required_telemetry():
    worker = EnrichmentWorker()

    with SessionLocal() as db:
        # Case A: Scrape Anti-bot failure -> marked unsupported, operation recorded as failed
        job_a = JobModel(
            title="Failed Scrape Job",
            company="FailCorp",
            source="test_phase5",
            source_job_id="p5_fail_1",
            discovered_at=datetime.now(timezone.utc),
            description="Snippet",
            description_is_snippet=True,
            url="https://example.com/fail"
        )
        db.add(job_a)
        db.flush()
        enr_a = JobEnrichmentModel(
            job_id=job_a.id,
            status="in_progress",
            lease_token="token_a",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            url=job_a.url
        )
        db.add(enr_a)
        db.commit()

        mock_req = httpx.Request("GET", "https://example.com/fail")
        mock_resp_403 = httpx.Response(403, request=mock_req)
        with patch.object(worker.ssrf_client, "fetch", side_effect=httpx.HTTPStatusError("403", request=mock_req, response=mock_resp_403)):
            with patch("app.services.scraper.firecrawl.FirecrawlClient.scrape") as mock_scrape:
                # Anti-bot marker returned
                mock_scrape.return_value = "Just a moment... Please enable cookies"
                worker.process_job(db, job_a.id, job_a.url, "token_a")

        enr_a_res = db.query(JobEnrichmentModel).filter(JobEnrichmentModel.job_id == job_a.id).first()
        assert enr_a_res.status == "unsupported"

        op_a = db.execute(text("""
            SELECT status, cost_units FROM firecrawl_operations
            WHERE operation = 'enrichment' ORDER BY id DESC LIMIT 1
        """)).mappings().first()
        assert op_a["status"] == "failed"
        assert op_a["cost_units"] == 1

        # Case B: Firecrawl HTTP 402 -> payment_required recorded, budget synced to ceiling
        job_b = JobModel(
            title="Payment Required Job",
            company="PayCorp",
            source="test_phase5",
            source_job_id="p5_pay_1",
            discovered_at=datetime.now(timezone.utc),
            description="Snippet",
            description_is_snippet=True,
            url="https://example.com/pay"
        )
        db.add(job_b)
        db.flush()
        enr_b = JobEnrichmentModel(
            job_id=job_b.id,
            status="in_progress",
            lease_token="token_b",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            url=job_b.url
        )
        db.add(enr_b)
        db.commit()

        mock_req_b = httpx.Request("GET", "https://example.com/pay")
        mock_resp_b = httpx.Response(403, request=mock_req_b)
        with patch.object(worker.ssrf_client, "fetch", side_effect=httpx.HTTPStatusError("403", request=mock_req_b, response=mock_resp_b)):
            with patch("app.services.scraper.firecrawl.FirecrawlClient.scrape") as mock_scrape:
                mock_scrape.side_effect = FirecrawlError("Payment required", status_code=402)
                worker.process_job(db, job_b.id, job_b.url, "token_b")

        enr_b_res = db.query(JobEnrichmentModel).filter(JobEnrichmentModel.job_id == job_b.id).first()
        assert enr_b_res.status == "unsupported"

        op_b = db.execute(text("""
            SELECT status, cost_units FROM firecrawl_operations
            WHERE operation = 'enrichment' ORDER BY id DESC LIMIT 1
        """)).mappings().first()
        assert op_b["status"] == "payment_required"
        assert op_b["cost_units"] == 1

        # Provider usage remains strictly at actual reserved credits (1 for job_a + 1 for job_b = 2), NEVER synthetic 1000
        synced_usage = db.execute(text("""
            SELECT request_count FROM provider_usage
            WHERE provider_name = 'firecrawl_monthly'
        """)).scalar()
        assert synced_usage == 2
        assert synced_usage < 1000

        # Subsequent reservation is blocked fail-closed
        from app.services.ingestion import acquire_provider_request_slot
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False


# 6. Deterministic Last Operation Ordering Under Identical Timestamps
def test_last_operation_deterministic_ordering_with_identical_timestamps():
    fixed_time = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        # Insert op 1
        db.execute(text("""
            INSERT INTO firecrawl_operations (operation, cost_units, status, created_at)
            VALUES ('discovery', 2, 'success', :t)
        """), {"t": fixed_time})

        # Insert op 2 with identical timestamp
        db.execute(text("""
            INSERT INTO firecrawl_operations (operation, cost_units, status, created_at)
            VALUES ('enrichment', 1, 'success', :t)
        """), {"t": fixed_time})
        db.commit()

        state = get_firecrawl_budget_state(db)
        last_op = state["last_operation"]
        assert last_op is not None
        # op 2 has higher id, so it must win deterministically
        assert last_op["operation"] == "enrichment"
        assert last_op["credits"] == 1
        assert last_op["status"] == "success"


# 7. GET /v1/system/status Additive Firecrawl Telemetry
def test_system_status_endpoint_additive_firecrawl():
    with SessionLocal() as db:
        record_firecrawl_operation(operation="discovery", cost_units=2, status="success")

    res = client.get("/v1/system/status")
    assert res.status_code == 200
    data = res.json()

    # Existing fields remain intact
    assert "engine_active" in data
    assert "last_sync" in data
    assert "latest_execution_status" in data
    assert "total_processed" in data

    # Additive firecrawl field
    assert "firecrawl" in data
    fc = data["firecrawl"]
    assert fc is not None
    assert "monthly_cap" in fc
    assert "monthly_used" in fc
    assert "monthly_remaining" in fc
    assert "reserved" in fc
    assert "discovery_used" in fc
    assert "enrichment_used" in fc
    assert "quota_denied" in fc
    assert "last_operation" in fc
    assert fc["last_operation"]["operation"] == "discovery"
    assert fc["last_operation"]["credits"] == 2


# 8. GET /ingestion/internal/firecrawl-telemetry Authentication and Output
def test_internal_firecrawl_telemetry_endpoint():
    # 401 on missing auth
    res_no_auth = client.get("/ingestion/internal/firecrawl-telemetry")
    assert res_no_auth.status_code == 401

    # 401 on wrong auth
    res_bad_auth = client.get("/ingestion/internal/firecrawl-telemetry", headers={"X-Api-Key": "wrong"})
    assert res_bad_auth.status_code == 401

    # 200 on valid auth
    headers = {"X-Api-Key": settings.api_secret_key}
    res_ok = client.get("/ingestion/internal/firecrawl-telemetry", headers=headers)
    assert res_ok.status_code == 200
    data = res_ok.json()

    assert data["monthly_cap"] == 900
    assert data["reserved"] == 100
    assert "monthly_used" in data
    assert "monthly_remaining" in data
    assert "discovery_used" in data
    assert "enrichment_used" in data
    assert "quota_denied" in data


# 9. Docker Compose & .env.example Contract Verification
def test_docker_compose_and_env_contract():
    # Check .env.example
    with open(".env.example", "r") as f:
        env_content = f.read()

    required_vars = [
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_ENABLED",
        "FIRECRAWL_MONTHLY_BUDGET",
        "FIRECRAWL_MONTHLY_AUTOMATION_CAP",
        "FIRECRAWL_RESERVED_CREDITS",
        "FIRECRAWL_DISCOVERY_ENABLED",
        "FIRECRAWL_DISCOVERY_MAX_QUERIES_PER_RUN",
        "FIRECRAWL_DISCOVERY_MAX_RESULTS_PER_QUERY",
        "FIRECRAWL_DISCOVERY_MAX_CREDITS_PER_RUN",
        "FIRECRAWL_ENRICHMENT_MAX_CREDITS_PER_RUN",
    ]
    for var in required_vars:
        assert var in env_content, f"Missing {var} in .env.example"

    # Ensure no actual secret was committed
    assert "__REPLACE_WITH_FIRECRAWL_KEY__" in env_content
    assert not re.search(r"FIRECRAWL_API_KEY=fc-[a-zA-Z0-9]+", env_content)

    # Required environment variables for services
    fastapi_expected = [
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_ENABLED",
        "FIRECRAWL_MONTHLY_BUDGET",
        "FIRECRAWL_MONTHLY_AUTOMATION_CAP",
        "FIRECRAWL_RESERVED_CREDITS",
        "FIRECRAWL_DISCOVERY_ENABLED",
        "FIRECRAWL_DISCOVERY_MAX_QUERIES_PER_RUN",
        "FIRECRAWL_DISCOVERY_MAX_RESULTS_PER_QUERY",
        "FIRECRAWL_DISCOVERY_MAX_CREDITS_PER_RUN",
    ]
    worker_expected = [
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_ENABLED",
        "FIRECRAWL_MONTHLY_BUDGET",
        "FIRECRAWL_MONTHLY_AUTOMATION_CAP",
        "FIRECRAWL_RESERVED_CREDITS",
        "FIRECRAWL_ENRICHMENT_MAX_CREDITS_PER_RUN",
    ]

    # Check docker-compose.yml
    with open("docker-compose.yml", "r") as f:
        dc = yaml.safe_load(f)

    fastapi_env = dc["services"]["fastapi"]["environment"]
    worker_env = dc["services"]["enrichment_worker"]["environment"]

    for var in fastapi_expected:
        assert any(var in e for e in fastapi_env), f"Missing {var} in docker-compose.yml fastapi"
    for var in worker_expected:
        assert any(var in e for e in worker_env), f"Missing {var} in docker-compose.yml enrichment_worker"

    # Check docker-compose.production.yml
    with open("docker-compose.production.yml", "r") as f:
        dc_prod = yaml.safe_load(f)

    prod_fastapi_env = dc_prod["services"]["fastapi"]["environment"]
    prod_worker_env = dc_prod["services"]["enrichment_worker"]["environment"]

    for var in fastapi_expected:
        assert any(var in e for e in prod_fastapi_env), f"Missing {var} in docker-compose.production.yml fastapi"
    for var in worker_expected:
        assert any(var in e for e in prod_worker_env), f"Missing {var} in docker-compose.production.yml enrichment_worker"


# 10. PostgreSQL Concurrency: Discovery & Enrichment Racing Across 900-Credit Boundary
def test_concurrent_discovery_and_enrichment_quota_race_postgresql():
    settings.firecrawl_monthly_automation_cap = 900
    settings.firecrawl_monthly_budget = 1000

    with SessionLocal() as db:
        # Seed 895 credits into provider_usage (leaving exactly 5 credits before 900 ceiling)
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', CURRENT_DATE, 895)
        """))
        db.commit()

    # Launch 5 discovery requests (2 credits each) and 5 enrichment requests (1 credit each)
    # Total requested: 10 + 5 = 15 credits against 5 available credits
    def attempt_discovery():
        with SessionLocal() as db:
            ok = acquire_provider_request_slot(db, "firecrawl", cost_units=2)
            return ("discovery", 2, ok)

    def attempt_enrichment():
        with SessionLocal() as db:
            ok = acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1)
            return ("enrichment", 1, ok)

    tasks = [attempt_discovery for _ in range(5)] + [attempt_enrichment for _ in range(5)]
    results = []

    # Real PostgreSQL connection pool with 10 concurrent threads racing on pg_advisory_xact_lock
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    accepted = [r for r in results if r[2] is True]
    rejected = [r for r in results if r[2] is False]

    accepted_credits = sum(cost for _, cost, _ in accepted)
    rejected_credits = sum(cost for _, cost, _ in rejected)

    assert len(results) == 10
    assert accepted_credits + rejected_credits == 15
    assert len(rejected) >= 1

    with SessionLocal() as db:
        total_used = db.execute(text("""
            SELECT COALESCE(SUM(request_count), 0) FROM provider_usage
            WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')
              AND usage_date >= DATE_TRUNC('month', CURRENT_DATE)::DATE
              AND usage_date < DATE_TRUNC('month', CURRENT_DATE)::DATE + INTERVAL '1 month'
        """)).scalar()

        # Under no circumstance can total_used exceed the 900 cap
        assert total_used <= 900
        # Authoritative credit balance: seeded + accepted == total_used
        assert 895 + accepted_credits == total_used

        # Denials must have been logged in firecrawl_operations matching rejected count exactly
        denials = db.execute(text("""
            SELECT COUNT(*) FROM firecrawl_operations WHERE status = 'denied'
        """)).scalar()
        assert denials == len(rejected)
        assert denials >= 1


# 11. Verified Pricing Semantics
def test_firecrawl_pricing_semantics():
    # Official Firecrawl pricing verified:
    # - Search: 2 credits per 10 results (rounded up)
    # - Scrape: 1 credit per page
    assert calculate_search_credits(0) == 2
    assert calculate_search_credits(1) == 2
    assert calculate_search_credits(10) == 2
    assert calculate_search_credits(11) == 4
    assert calculate_search_credits(20) == 4
    assert calculate_search_credits(21) == 6
    assert calculate_search_credits(100) == 20


# 12. Payment Required Exhaustion State Blocks Subsequent Reservations Without Corrupting Usage
def test_payment_required_state_blocks_subsequent_reservations_without_corrupting_usage():
    with SessionLocal() as db:
        settings.firecrawl_enabled = True
        settings.firecrawl_api_key = "fc-test-key"
        settings.firecrawl_monthly_budget = 1000

        # Pre-condition: 5 real credits recorded in provider_usage
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl_monthly', DATE_TRUNC('month', CURRENT_DATE)::DATE, 5)
        """))
        db.commit()

        # Record payment_required in firecrawl_operations
        record_firecrawl_operation(operation="enrichment", cost_units=1, status="payment_required")

        # Verify provider_usage is completely unchanged (still 5)
        usage = db.execute(text("""
            SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly'
        """)).scalar()
        assert usage == 5

        # Check budget state
        state = get_firecrawl_budget_state(db)
        assert state["monthly_used"] == 5
        assert state["enrichment_used"] == 5
        assert state["monthly_remaining"] == 895
        assert state["is_exhausted"] is True

        # Subsequent reservation must fail closed (return False)
        from app.services.ingestion import acquire_provider_request_slot
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is False

        # Usage in provider_usage must still remain exactly 5
        usage_after = db.execute(text("""
            SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly'
        """)).scalar()
        assert usage_after == 5


# 13. Payment Required Survives Quota Transaction Rollback
def test_payment_required_survives_rollback_independently():
    with SessionLocal() as db:
        # In a transaction that rolls back:
        try:
            db.execute(text("""
                INSERT INTO provider_usage (provider_name, usage_date, request_count)
                VALUES ('firecrawl_monthly', DATE_TRUNC('month', CURRENT_DATE)::DATE, 99)
            """))
            # Call record_firecrawl_operation inside this block
            record_firecrawl_operation(operation="enrichment", cost_units=1, status="payment_required")
            # Now raise to force rollback
            raise RuntimeError("Simulated transaction failure")
        except RuntimeError:
            db.rollback()

        # The 99 in provider_usage was rolled back
        usage = db.execute(text("""
            SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly'
        """)).scalar()
        assert usage is None

        # But the payment_required event in firecrawl_operations persisted independently
        op = db.execute(text("""
            SELECT status FROM firecrawl_operations WHERE status = 'payment_required'
        """)).scalar()
        assert op == "payment_required"


# 14. Consecutive claim_and_process Batch Cap Isolation
def test_consecutive_claim_and_process_batch_cap_isolation():
    with SessionLocal() as db:
        settings.firecrawl_enabled = True
        settings.firecrawl_api_key = "fc-test-key"
        settings.firecrawl_monthly_budget = 1000
        settings.firecrawl_enrichment_max_credits_per_run = 5

        worker = EnrichmentWorker(claim_batch_size=5)

        # Insert 10 jobs needing fallback
        for i in range(10):
            job = JobModel(
                title=f"Batch Job {i}",
                company="BatchCorp",
                source="test_phase5",
                source_job_id=f"batch_job_{i}_{uuid.uuid4().hex[:6]}",
                discovered_at=datetime.now(timezone.utc),
                description="Snippet",
                description_is_snippet=True,
                url=f"https://example.com/batch/{i}"
            )
            db.add(job)
            db.flush()
            db.add(JobEnrichmentModel(
                job_id=job.id,
                status="pending",
                url=job.url
            ))
        db.commit()

        mock_req = httpx.Request("GET", "https://example.com/batch")
        mock_resp = httpx.Response(403, request=mock_req)

        with patch.object(worker.ssrf_client, "fetch", side_effect=httpx.HTTPStatusError("403", request=mock_req, response=mock_resp)):
            with patch("app.services.scraper.firecrawl.FirecrawlClient.scrape") as mock_scrape:
                mock_scrape.return_value = "Long description content exceeding 200 characters " * 6

                # Batch 1: Processes first 5 jobs, uses 5 credits
                worker.claim_and_process(db)
                assert worker.credits_used_this_run == 5
                assert mock_scrape.call_count == 5

                # Batch 2: Resets credits_used_this_run to 0 at execution-unit boundary, processes next 5 jobs
                worker.claim_and_process(db)
                assert worker.credits_used_this_run == 5
                assert mock_scrape.call_count == 10

        # Total credits reserved in provider_usage across both batch executions is 10
        total_usage = db.execute(text("""
            SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly'
        """)).scalar()
        assert total_usage == 10


# 15. The 900-Credit Hard Cap Blocks Reservations
def test_900_cap_blocks_reservations_after_402_state_handling():
    with SessionLocal() as db:
        settings.firecrawl_enabled = True
        settings.firecrawl_api_key = "fc-test-key"
        settings.firecrawl_monthly_budget = 1000

        # Insert 900 credits in provider_usage
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl_monthly', DATE_TRUNC('month', CURRENT_DATE)::DATE, 900)
        """))
        db.commit()

        from app.services.ingestion import acquire_provider_request_slot
        # Even without payment_required, 900 cap blocks reservation
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is False


# 16. Reconcile FirecrawlOperationModel Indexes with Alembic Migration
def test_firecrawl_operation_model_indexes_reconciled_with_alembic():
    # Verify ORM model declares both indexes matching Alembic migration 5a11c0000001
    model_indexes = {idx.name: idx for idx in FirecrawlOperationModel.__table__.indexes}
    assert "idx_firecrawl_ops_created_status" in model_indexes
    assert "idx_firecrawl_ops_order" in model_indexes

    # Verify column expressions
    created_status_cols = [c.name for c in model_indexes["idx_firecrawl_ops_created_status"].columns]
    assert created_status_cols == ["created_at", "status"]


# 17. Concurrent 402 Exhaustion Tightening Under Advisory Lock
def test_concurrent_402_exhaustion_tightening():
    settings.firecrawl_enabled = True
    settings.firecrawl_api_key = "fc-test-key"
    settings.firecrawl_monthly_budget = 1000

    # Ensure clean slate for test
    with SessionLocal() as db:
        clear_test_db(db)

        # Ordering boundary proof: A reservation that acquires the advisory lock
        # BEFORE the 402 transition is allowed to succeed and consume actual credits.
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is True

    # Setup synchronization events to keep the 402 transaction open during contention proof
    lock_staged_event = threading.Event()
    commit_allowed_event = threading.Event()
    orig_commit = Session.commit
    thread_402 = None

    def hooked_commit(self_session):
        if threading.current_thread() == thread_402:
            # 402 path has acquired advisory lock and staged payment_required into the open session
            lock_staged_event.set()
            if not commit_allowed_event.wait(timeout=10.0):
                raise TimeoutError("Timed out waiting for commit_allowed_event")
        return orig_commit(self_session)

    def run_402_sync():
        worker = EnrichmentWorker()
        with SessionLocal() as db_402:
            worker._sync_firecrawl_budget(db_402)

    with patch.object(Session, "commit", hooked_commit):
        thread_402 = threading.Thread(target=run_402_sync)
        thread_402.start()

        try:
            # Wait until the 402 transition has acquired the Firecrawl advisory lock
            assert lock_staged_event.wait(timeout=5.0), "402 transition failed to acquire advisory lock in time"

            # Start real reservation sessions while the 402 advisory lock is held
            reservations = [
                ("firecrawl_monthly", 1),
                ("firecrawl", 2),
                ("firecrawl_monthly", 1),
            ]
            results = {}

            def reservation_task(idx, p_name, cost):
                with SessionLocal() as db_worker:
                    results[idx] = acquire_provider_request_slot(db_worker, p_name, cost_units=cost)

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(reservations)) as executor:
                futures = [executor.submit(reservation_task, i, p, c) for i, (p, c) in enumerate(reservations)]

                # Explicitly prove via pg_locks that all reservation sessions are blocked on the advisory lock
                deadline = time.time() + 5.0
                blocked_count = 0
                with SessionLocal() as s_monitor:
                    while time.time() < deadline:
                        blocked_count = s_monitor.execute(text("""
                            SELECT COUNT(*) FROM pg_locks
                            WHERE locktype = 'advisory'
                              AND classid = hashtext('provider_quota')
                              AND objid = hashtext('firecrawl')
                              AND granted = false
                        """)).scalar() or 0
                        if blocked_count == len(reservations):
                            break
                        time.sleep(0.05)

                assert blocked_count == len(reservations), f"Expected {len(reservations)} blocked sessions, found {blocked_count}"
                assert len(results) == 0, "No reservation session should complete before the 402 transition commits"

                # Release the 402 transaction to commit payment_required and release the advisory lock
                commit_allowed_event.set()
                thread_402.join(timeout=5.0)
                assert not thread_402.is_alive(), "thread_402 should have finished"

                # Blocked reservations wake and finish
                for f in futures:
                    f.result(timeout=5.0)

            # Assert each reservation that began while the 402 lock was held returns False
            for idx in range(len(reservations)):
                assert results[idx] is False

            with SessionLocal() as db:
                # Operational exhaustion state is active
                state = get_firecrawl_budget_state(db)
                assert state["is_exhausted"] is True

                # Assert provider_usage contains only actual reservations (the 1 pre-402 credit) and never synthetic 1000 credits
                fc_usage = db.execute(text("""
                    SELECT COALESCE(SUM(request_count), 0) FROM provider_usage
                    WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')
                """)).scalar() or 0
                assert fc_usage == 1
                assert fc_usage < 1000

                # Assert the payment_required event persists independently along with the denied operational events
                payment_req_count = db.execute(text("""
                    SELECT COUNT(*) FROM firecrawl_operations WHERE status = 'payment_required'
                """)).scalar() or 0
                denied_count = db.execute(text("""
                    SELECT COUNT(*) FROM firecrawl_operations WHERE status = 'denied'
                """)).scalar() or 0
                assert payment_req_count == 1
                assert denied_count >= len(reservations)

                # Subsequent reservations fail closed immediately
                assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False
                assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is False

        finally:
            commit_allowed_event.set()
            if thread_402 and thread_402.is_alive():
                thread_402.join(timeout=2.0)


# 18. Telemetry Persistence Error Graceful Handling
def test_telemetry_persistence_error_graceful_handling():
    # If SessionLocal fails during telemetry recording, it should log error and not crash caller
    with patch("app.db.database.SessionLocal", side_effect=RuntimeError("Simulated DB connection error")):
        # Must not raise RuntimeError
        record_firecrawl_operation(operation="enrichment", cost_units=1, status="success")
        record_firecrawl_operation(operation="enrichment", cost_units=1, status="payment_required")
