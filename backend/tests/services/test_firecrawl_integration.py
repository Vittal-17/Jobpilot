import pytest
from unittest.mock import patch
from app.services.enrichment_worker import EnrichmentWorker
from app.db.database import SessionLocal
from sqlalchemy import text
from app.core.config import settings
from app.services.scraper.firecrawl import FirecrawlError
import concurrent.futures
import httpx
import uuid
import respx

def clear_db():
    db = SessionLocal()
    # Clean up quota rows
    db.execute(text("DELETE FROM provider_usage WHERE provider_name = 'firecrawl_monthly'"))
    # Clean up any jobs created by firecrawl tests (using source = 'test_firecrawl' or test prefixes)
    db.execute(text("DELETE FROM job_enrichments WHERE job_id IN (SELECT id FROM jobs WHERE source = 'test_firecrawl' OR source_job_id LIKE 'test_fc_%')"))
    db.execute(text("DELETE FROM jobs WHERE source = 'test_firecrawl' OR source_job_id LIKE 'test_fc_%'"))
    # Also clean up legacy test IDs if present
    db.execute(text("DELETE FROM job_enrichments WHERE job_id IN (101, 102, 103, 104, 200)"))
    db.execute(text("DELETE FROM jobs WHERE id IN (101, 102, 103, 104, 200)"))
    db.commit()
    db.close()

@pytest.fixture(autouse=True)
def run_around_tests():
    clear_db()
    yield
    clear_db()

def test_quota_atomicity_and_persistence():
    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = 5

    def attempt_reservation():
        # New DB session for each thread to simulate concurrent workers
        db = SessionLocal()
        worker = EnrichmentWorker(max_attempts=3)
        res = worker._reserve_firecrawl_credit(db)
        db.close()
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: attempt_reservation(), range(10)))

    successes = [r for r in results if r]
    assert len(successes) == 5

    # Test persistence (restart)
    db = SessionLocal()
    worker2 = EnrichmentWorker(max_attempts=3)
    assert worker2._reserve_firecrawl_credit(db) is False # already at 5
    db.close()

def test_monthly_rollover():
    db = SessionLocal()
    # Mocking CURRENT_DATE is hard in Postgres, so we just insert a row for last month
    db.execute(text("""
        INSERT INTO provider_usage (provider_name, usage_date, request_count)
        VALUES ('firecrawl_monthly', DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')::DATE, 5)
    """))
    db.commit()

    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = 5

    worker = EnrichmentWorker(max_attempts=3)
    assert worker._reserve_firecrawl_credit(db) is True

    current = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE")).scalar()
    assert current == 1
    db.close()

def test_rollback_safety():
    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = 1000

    db = SessionLocal()
    worker = EnrichmentWorker(max_attempts=3)

    # Pre-condition: reserve 1 credit
    res = worker._reserve_firecrawl_credit(db)
    assert res is True

    current = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE")).scalar()
    assert current == 1

    src_id = f"test_fc_rollback_{uuid.uuid4().hex}"
    # Perform representative enrichment DB work in the primary session
    job_id = db.execute(text("""
        INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at)
        VALUES ('Software Engineer', 'Tech Corp', 'test_firecrawl', :src_id, NOW(), NOW(), NOW())
        RETURNING id
    """), {"src_id": src_id}).scalar()
    assert job_id is not None

    db.execute(text("""
        INSERT INTO job_enrichments (job_id, status, url, created_at, updated_at)
        VALUES (:job_id, 'in_progress', 'http://a.com', NOW(), NOW())
    """), {"job_id": job_id})
    db.flush()

    # Deliberately roll back the primary transaction (e.g. simulating a crash during LLM recommendation matching)
    db.rollback()

    # Assert Firecrawl usage remains committed because it executed in an isolated SessionLocal
    usage = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE")).scalar()
    assert usage == 1

    # Verify the primary session work was successfully rolled back
    job_exists = db.execute(text("SELECT 1 FROM jobs WHERE source_job_id = :src_id"), {"src_id": src_id}).scalar()
    assert job_exists is None
    enrichment_exists = db.execute(text("SELECT 1 FROM job_enrichments WHERE job_id = :job_id"), {"job_id": job_id}).scalar()
    assert enrichment_exists is None

    db.close()


@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
def test_disabled_configurations_no_side_effects(mock_scrape):
    db = SessionLocal()
    worker = EnrichmentWorker(max_attempts=3)

    configs = [
        (None, 1000),      # missing API key
        ("", 1000),        # empty API key
        ("test_key", 0),   # zero budget
        ("test_key", -10)  # negative budget
    ]

    for idx, (api_key, budget) in enumerate(configs, start=1):
        settings.firecrawl_api_key = api_key
        settings.firecrawl_monthly_budget = budget

        # Setup collision-free job and job_enrichment for process_job
        src_id = f"test_fc_disabled_{idx}_{uuid.uuid4().hex}"
        token = str(uuid.uuid4())
        job_id = db.execute(text("""
            INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at)
            VALUES ('Title', 'Comp', 'test_firecrawl', :src_id, NOW(), NOW(), NOW())
            RETURNING id
        """), {"src_id": src_id}).scalar()

        db.execute(text("""
            INSERT INTO job_enrichments (job_id, status, url, lease_token, lease_expires_at, attempts, created_at, updated_at)
            VALUES (:id, 'in_progress', 'http://a.com', :token, NOW() + INTERVAL '1 hour', 0, NOW(), NOW())
        """), {"id": job_id, "token": token})
        db.commit()

        # Mock native fetch to fail so it drops to Firecrawl
        with patch.object(worker.ssrf_client, 'fetch') as mock_fetch:
            mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
            mock_fetch.side_effect = httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)

            # Should fallback to snippet because Firecrawl is disabled
            worker.process_job(db, job_id, "http://a.com", token)

            # Assert snippet fallback is invoked (status='unsupported')
            status = db.execute(text("SELECT status FROM job_enrichments WHERE job_id = :id"), {"id": job_id}).scalar()
            assert status == 'unsupported', f"Config (api_key={api_key}, budget={budget}) failed to drop to unsupported"

    # Assert network scrape was never called for any config
    mock_scrape.assert_not_called()

    # Assert DB has no quota rows for firecrawl
    usage = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE")).scalar()
    assert usage is None

    db.close()


@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
def test_real_402_synchronization_via_process_job(mock_scrape):
    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = 1000

    db = SessionLocal()
    worker = EnrichmentWorker(max_attempts=3)

    # Setup collision-free job and job_enrichment
    src_id = f"test_fc_402_{uuid.uuid4().hex}"
    token = str(uuid.uuid4())
    job_id = db.execute(text("""
        INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at)
        VALUES ('Title', 'Comp', 'test_firecrawl', :src_id, NOW(), NOW(), NOW())
        RETURNING id
    """), {"src_id": src_id}).scalar()

    db.execute(text("""
        INSERT INTO job_enrichments (job_id, status, url, lease_token, lease_expires_at, attempts, created_at, updated_at)
        VALUES (:id, 'in_progress', 'http://a.com', :token, NOW() + INTERVAL '1 hour', 0, NOW(), NOW())
    """), {"id": job_id, "token": token})
    db.commit()

    # Pre-condition: manually insert 1 credit to prove it jumps to 1000
    db.execute(text("""
        INSERT INTO provider_usage (provider_name, usage_date, request_count)
        VALUES ('firecrawl_monthly', DATE_TRUNC('month', CURRENT_DATE)::DATE, 1)
        ON CONFLICT (provider_name, usage_date) DO UPDATE SET request_count = 1
    """))
    db.commit()

    current = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE")).scalar()
    assert current == 1

    with patch.object(worker.ssrf_client, 'fetch') as mock_fetch:
        mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
        mock_fetch.side_effect = httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)

        # Make scrape raise 402
        mock_scrape.side_effect = FirecrawlError("Exhausted", status_code=402)

        # This will invoke `_sync_firecrawl_budget(db)`
        worker.process_job(db, job_id, "http://a.com", token)
        mock_scrape.assert_called_once()

    # Assert DB is now at 1000
    synced = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE")).scalar()
    assert synced == 1000

    # Also assert status is 'unsupported'
    status = db.execute(text("SELECT status FROM job_enrichments WHERE job_id = :id"), {"id": job_id}).scalar()
    assert status == 'unsupported'

    db.close()


@respx.mock
def test_transport_error_retry_state_transition():
    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = 1000

    db = SessionLocal()
    worker = EnrichmentWorker(max_attempts=3)

    src_id = f"test_fc_transport_{uuid.uuid4().hex}"
    token = str(uuid.uuid4())
    job_id = db.execute(text("""
        INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at)
        VALUES ('Transport Job', 'Transport Co', 'test_firecrawl', :src_id, NOW(), NOW(), NOW())
        RETURNING id
    """), {"src_id": src_id}).scalar()

    db.execute(text("""
        INSERT INTO job_enrichments (job_id, status, url, lease_token, lease_expires_at, attempts, created_at, updated_at)
        VALUES (:id, 'in_progress', 'http://blocked.com', :token, NOW() + INTERVAL '1 hour', 0, NOW(), NOW())
    """), {"id": job_id, "token": token})
    db.commit()

    # Pre-condition: check attempts=0, status='in_progress'
    initial_row = db.execute(text("SELECT status, attempts, error_reason FROM job_enrichments WHERE job_id = :id"), {"id": job_id}).fetchone()
    assert initial_row[0] == "in_progress"
    assert initial_row[1] == 0
    assert initial_row[2] is None

    # Mock Firecrawl endpoint with respx to simulate actual transport error
    respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        side_effect=httpx.RequestError("Connection reset by peer")
    )

    # Native fetch returns 403 to trigger Firecrawl fallback
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        # Execute process_job without mocking complete_failure: real state transition occurs
        worker.process_job(db, job_id, "http://blocked.com", token)

    # Verify actual database state transition:
    # 1. status -> 'retry' (transient, NOT 'unsupported' and NOT 'failure')
    # 2. attempts incremented from 0 to 1
    # 3. error_reason reflects the Firecrawl transient transport error
    # 4. lease remains intact for worker reclamation
    post_retry_row = db.execute(text("""
        SELECT status, attempts, error_reason, lease_token, lease_expires_at > clock_timestamp()
        FROM job_enrichments
        WHERE job_id = :id
    """), {"id": job_id}).fetchone()

    assert post_retry_row[0] == "retry"
    assert post_retry_row[1] == 1
    assert "Firecrawl transient error" in post_retry_row[2]
    assert "Connection reset by peer" in post_retry_row[2]
    assert post_retry_row[3] == token
    assert post_retry_row[4] is True

    # Now verify the max_attempts exhaustion transition:
    # If the job had already failed (attempts = 2 with max_attempts = 3)
    db.execute(text("UPDATE job_enrichments SET status = 'in_progress', attempts = 2 WHERE job_id = :id"), {"id": job_id})
    db.commit()

    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        worker.process_job(db, job_id, "http://blocked.com", token)

    final_row = db.execute(text("""
        SELECT status, attempts, error_reason
        FROM job_enrichments
        WHERE job_id = :id
    """), {"id": job_id}).fetchone()

    assert final_row[0] == "failure"  # Terminal failure after exhausting max_attempts
    assert final_row[1] == 3          # Attempts reached max_attempts (3)

    db.close()
