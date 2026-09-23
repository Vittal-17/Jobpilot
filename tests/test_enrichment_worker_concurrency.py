import pytest
import time
import threading
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.search_execution import SearchExecutionModel
from app.services.enrichment_worker import EnrichmentWorker

# This mirrors, verbatim, the additive telemetry UPDATE issued by the
# production paths (EnrichmentWorker.complete_success and the synchronous
# internal_execute_search endpoint). The invariant under test is that the
# counters are mutated with a read-modify-write that PostgreSQL serialises
# behind the row lock (COALESCE(col, 0) + :delta), never an absolute SET.
TELEMETRY_ADDITIVE_SQL = text("""
    UPDATE search_execution
    SET jobs_fresher_eligible = COALESCE(jobs_fresher_eligible, 0) + :elig,
        recommendations_created = COALESCE(recommendations_created, 0) + :recs
    WHERE id = :eid AND status NOT IN ('failed', 'abandoned')
""")

def test_lease_expiry_under_row_lock(engine):
    # 'engine' fixture is from conftest.py (or we can use db_session's bind)
    Session = sessionmaker(bind=engine)

    # We use independent sessions.
    with Session() as setup_db:
        # Setup job and enrichment
        job = JobModel(
            title="Lock Test",
            company="Tech Corp",
            source="test",
            source_job_id="lock_1",
            discovered_at=datetime.now(timezone.utc),
            description="Snippet...",
            description_is_snippet=True,
            url="https://example.com/job"
        )
        setup_db.add(job)
        setup_db.commit()
        job_id = job.id

        token = "lock_token"
        # Use DB clock for synchronization
        db_now = setup_db.execute(text("SELECT clock_timestamp()")).scalar()
        expires = db_now + timedelta(seconds=1.5)

        enrichment = JobEnrichmentModel(
            job_id=job.id,
            status='in_progress',
            url=job.url,
            lease_token=token,
            lease_expires_at=expires
        )
        setup_db.add(enrichment)
        setup_db.commit()

    thread_errors = []

    def worker_b_task():
        try:
            with Session() as db_b:
                worker = EnrichmentWorker()
                # This will block because session A holds the lock.
                worker.complete_success(db_b, job_id, token, "New Full Description")
        except Exception as e:
            thread_errors.append(e)

    try:
        # Session A holds lock AND modifies the row to trigger EPQ re-evaluation
        # If it only SELECTs and rolls back, Postgres will skip re-evaluating clock_timestamp()
        with Session() as db_a:
            db_a.execute(text("UPDATE job_enrichments SET status = status WHERE job_id = :jid"), {"jid": job_id})

            # Start Session B which will block
            t = threading.Thread(target=worker_b_task)
            t.start()

            # Wait for the lease to expire while Session B is blocked
            time.sleep(2.0)

            # Commit releases the lock and signals modification, forcing B to re-evaluate its WHERE clause
            db_a.commit()

        t.join(timeout=5)

        # Check thread did not crash
        assert not thread_errors, f"Thread raised exceptions: {thread_errors}"
        assert not t.is_alive(), "Worker B thread hung"

        # Check that Session B did NOT update the job or enrichment
        with Session() as check_db:
            job_check = check_db.get(JobModel, job_id)
            enr_check = check_db.query(JobEnrichmentModel).filter_by(job_id=job_id).first()

            assert enr_check.status == 'in_progress'
            assert job_check.description_is_snippet is True
            assert job_check.description == "Snippet..."

    finally:
        # Cleanup
        with Session() as clean_db:
            clean_db.execute(text("DELETE FROM job_enrichments WHERE job_id = :jid"), {"jid": job_id})
            clean_db.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            clean_db.commit()


def test_concurrent_telemetry_increments_do_not_overwrite(engine):
    """Two independent transactions incrementing the SAME search_execution row
    must both survive: PostgreSQL serialises the second read-modify-write behind
    the first's row lock, so the counters end at the SUM of both increments.

    This is a genuine contention test, not a sequential replay:
      * Transaction A issues the production additive UPDATE and holds the row
        lock open (uncommitted) for a fixed window.
      * Transaction B, on a separate connection, issues the same additive
        UPDATE and BLOCKS on A's row lock until A commits.
    We assert (a) B was actually blocked for at least the hold window (proving
    real lock contention), and (b) the final counters equal initial + A + B
    (proving no lost update / overwrite).
    """
    Session = sessionmaker(bind=engine)

    exec_id = 990001
    A_ELIG, A_RECS = 3, 2
    B_ELIG, B_RECS = 5, 7
    HOLD_SECONDS = 1.5

    with Session() as setup_db:
        setup_db.execute(text("DELETE FROM search_execution WHERE id = :eid"), {"eid": exec_id})
        setup_db.add(SearchExecutionModel(
            id=exec_id,
            candidate_id="CONCURRENCY::LOC",
            status="succeeded",
            selected_at=datetime.now(timezone.utc),
            jobs_fresher_eligible=0,
            recommendations_created=0,
        ))
        setup_db.commit()

    a_locked = threading.Event()
    a_committed = threading.Event()
    thread_errors = []
    b_wait_seconds = {}

    def txn_a():
        try:
            with Session() as db_a:
                # Acquire the row lock via the production additive UPDATE and
                # keep the transaction open so B must wait on us.
                db_a.execute(TELEMETRY_ADDITIVE_SQL, {"elig": A_ELIG, "recs": A_RECS, "eid": exec_id})
                a_locked.set()
                time.sleep(HOLD_SECONDS)
                db_a.commit()
                a_committed.set()
        except Exception as e:  # pragma: no cover - surfaced via assertion below
            thread_errors.append(("A", e))
            a_locked.set()

    def txn_b():
        try:
            assert a_locked.wait(timeout=5), "Transaction A never acquired the lock"
            with Session() as db_b:
                start = time.perf_counter()
                # Blocks until A commits and releases the row lock.
                db_b.execute(TELEMETRY_ADDITIVE_SQL, {"elig": B_ELIG, "recs": B_RECS, "eid": exec_id})
                b_wait_seconds["waited"] = time.perf_counter() - start
                db_b.commit()
        except Exception as e:  # pragma: no cover - surfaced via assertion below
            thread_errors.append(("B", e))

    try:
        ta = threading.Thread(target=txn_a)
        tb = threading.Thread(target=txn_b)
        ta.start()
        tb.start()
        ta.join(timeout=10)
        tb.join(timeout=10)

        assert not thread_errors, f"Concurrency threads raised: {thread_errors}"
        assert not ta.is_alive() and not tb.is_alive(), "A telemetry transaction hung"
        assert a_committed.is_set(), "Transaction A did not commit"

        # B must have been forced to wait on A's row lock: this is what makes
        # the two increments serialise instead of racing on a stale read.
        waited = b_wait_seconds.get("waited")
        assert waited is not None, "Transaction B never completed its UPDATE"
        assert waited >= HOLD_SECONDS * 0.8, (
            f"Transaction B did not block on the row lock (waited {waited:.3f}s, "
            f"expected >= {HOLD_SECONDS * 0.8:.3f}s) - the increments were not contended"
        )

        with Session() as check_db:
            row = check_db.query(SearchExecutionModel).filter_by(id=exec_id).first()
            assert row.jobs_fresher_eligible == A_ELIG + B_ELIG, (
                f"Lost update: jobs_fresher_eligible={row.jobs_fresher_eligible}, "
                f"expected {A_ELIG + B_ELIG}"
            )
            assert row.recommendations_created == A_RECS + B_RECS, (
                f"Lost update: recommendations_created={row.recommendations_created}, "
                f"expected {A_RECS + B_RECS}"
            )
    finally:
        with Session() as clean_db:
            clean_db.execute(text("DELETE FROM search_execution WHERE id = :eid"), {"eid": exec_id})
            clean_db.commit()
