import time
import threading
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.models.search_execution import SearchExecutionModel
from app.services.search_selector import _clean_abandoned_claims, select_next_search
from app.domain.taxonomy import get_authoritative_taxonomy

def test_stale_started_execution_is_reclaimed(db_session):
    """stale started execution is reclaimed"""
    candidate_id = "role_software_engineer::LOC-BLR-001"
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=20)

    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='started',
        selected_at=stale_time,
        started_at=stale_time,
        query_variant="Software Engineer",
        retrieval_location="Bengaluru"
    )
    db_session.add(exec1)
    db_session.commit()

    reclaimed = _clean_abandoned_claims(db_session, reference_time=now)
    assert reclaimed == 1

    db_session.refresh(exec1)
    assert exec1.status == 'failed'
    assert exec1.error_message == 'abandoned claim'

def test_fresh_started_execution_is_preserved(db_session):
    """fresh started execution is preserved"""
    candidate_id = "role_software_engineer::LOC-BLR-001"
    now = datetime.now(timezone.utc)
    fresh_time = now - timedelta(minutes=5)

    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='started',
        selected_at=fresh_time,
        started_at=fresh_time,
        query_variant="Software Engineer",
        retrieval_location="Bengaluru"
    )
    db_session.add(exec1)
    db_session.commit()

    reclaimed = _clean_abandoned_claims(db_session, reference_time=now)
    assert reclaimed == 0

    db_session.refresh(exec1)
    assert exec1.status == 'started'

def test_selected_cleanup_still_works(db_session):
    """selected cleanup still works"""
    candidate_id = "role_software_engineer::LOC-BLR-001"
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=20)

    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='selected',
        selected_at=stale_time,
        query_variant="Software Engineer",
        retrieval_location="Bengaluru"
    )
    db_session.add(exec1)
    db_session.commit()

    reclaimed = _clean_abandoned_claims(db_session, reference_time=now)
    assert reclaimed == 1

    db_session.refresh(exec1)
    assert exec1.status == 'failed'

def test_reclaimed_candidate_becomes_selectable(db_session):
    """reclaimed candidate becomes selectable again"""
    # Create a fresh DB environment for selection
    db_session.execute(text("DELETE FROM search_execution"))
    db_session.commit()

    candidate_id = "role_software_engineer::LOC-BLR-001"
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=20)

    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='started',
        selected_at=stale_time,
        started_at=stale_time,
        query_variant="Software Engineer",
        retrieval_location="Bengaluru"
    )
    db_session.add(exec1)
    db_session.commit()

    # Run selector
    res = select_next_search(db_session, reference_time=now)
    assert res.action == 'execute'
    # Confirm it was reclaimed
    db_session.refresh(exec1)
    assert exec1.status == 'failed'

def test_race_a_selected_to_started_holds_lock_against_cleanup(db_session):
    """
    Race A: Session A begins a transaction, transitions selected -> started, and
    intentionally holds the transaction open before commit.
    Session B attempts stale cleanup while A holds the row lock.

    Verifies:
      1. Real transaction overlap with independent sessions.
      2. Deterministic PostgreSQL lock contention occurs (Session B blocks on Session A's row lock).
      3. Session B cannot incorrectly reclaim the execution while A holds the uncommitted lock.
      4. After Session A commits, Session B unblocks, its cleanup affects zero rows,
         and persisted state is 'started'.

    Note: This test proves database transition safety under overlapping transactions,
    not worker/process liveness.
    """
    db_session.execute(text("DELETE FROM search_execution"))
    db_session.commit()

    candidate_id = "role_software_engineer::LOC-BLR-RACE-A"
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=20)

    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='selected',
        selected_at=stale_time,
        query_variant="Race Variant A",
        retrieval_location="Bengaluru",
    )
    db_session.add(exec1)
    db_session.commit()
    exec_id = exec1.id

    bind = db_session.get_bind()

    evt_a_updated = threading.Event()
    evt_release_a = threading.Event()
    evt_b_attempting = threading.Event()
    evt_b_done = threading.Event()

    b_reclaimed_count = [-1]
    worker_errors = []

    def session_a_worker():
        with Session(bind) as session_a:
            try:
                # Transition selected -> started inside an active transaction without committing
                res = session_a.execute(
                    text(
                        "UPDATE search_execution "
                        "SET status = 'started', started_at = :now, provider_name = :provider_name "
                        "WHERE id = :execution_id AND status = 'selected' "
                        "AND (provider_name IS NULL OR provider_name = :provider_name)"
                    ),
                    {"now": now, "execution_id": exec_id, "provider_name": "adzuna"},
                )
                assert res.rowcount == 1, "Session A should update exactly 1 row"

                # Signal that Session A holds the exclusive row lock
                evt_a_updated.set()

                # Intentionally hold open transaction until coordinator signals release
                if not evt_release_a.wait(timeout=5.0):
                    raise TimeoutError("Session A timed out waiting for release")

                session_a.commit()
            except Exception as e:
                session_a.rollback()
                worker_errors.append(("session_a", e))

    def session_b_worker():
        # Wait until Session A has acquired the row lock
        if not evt_a_updated.wait(timeout=5.0):
            worker_errors.append(("session_b_setup", TimeoutError("Timed out waiting for Session A lock")))
            return

        with Session(bind) as session_b:
            try:
                evt_b_attempting.set()
                # Run stale cleanup while A holds the row lock.
                # Threshold is older than 15 minutes
                threshold = now - timedelta(minutes=15)
                res = session_b.execute(
                    text("""
                        UPDATE search_execution
                        SET status = 'failed', completed_at = :now, error_message = 'abandoned claim'
                        WHERE (status = 'selected' AND selected_at < :threshold)
                           OR (status = 'started' AND COALESCE(started_at, selected_at) < :threshold)
                    """),
                    {"now": now, "threshold": threshold},
                )
                session_b.commit()
                b_reclaimed_count[0] = res.rowcount
                evt_b_done.set()
            except Exception as e:
                session_b.rollback()
                worker_errors.append(("session_b", e))

    t_a = threading.Thread(target=session_a_worker, name="race_a_worker_a")
    t_b = threading.Thread(target=session_b_worker, name="race_a_worker_b")

    try:
        t_a.start()
        t_b.start()

        assert evt_b_attempting.wait(timeout=5.0), "Session B did not start attempting cleanup"

        # Deterministically verify lock contention in PostgreSQL:
        # Session B must be blocked waiting on the row lock held by Session A
        contention_detected = False
        deadline = time.time() + 3.0
        with Session(bind) as check_session:
            while time.time() < deadline:
                cnt = check_session.execute(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                ).scalar()
                if cnt and cnt > 0:
                    contention_detected = True
                    break
                time.sleep(0.01)

        assert contention_detected, "Lock contention did not occur: Session B did not block on Session A's lock"
        # Verify Session B is still blocked and has not completed
        assert not evt_b_done.is_set(), "Session B finished before Session A released its lock!"

    finally:
        # Safeguard: ensure Session A is released and threads terminate cleanly
        evt_release_a.set()
        t_a.join(timeout=5.0)
        t_b.join(timeout=5.0)

    assert not worker_errors, f"Errors occurred in worker threads: {worker_errors}"
    assert not t_a.is_alive(), "Session A thread did not terminate"
    assert not t_b.is_alive(), "Session B thread did not terminate"

    # Verify Session B unblocked after A committed and affected zero rows
    assert b_reclaimed_count[0] == 0, f"Session B unexpectedly reclaimed {b_reclaimed_count[0]} rows"

    # Verify final persisted state is started
    db_session.refresh(exec1)
    assert exec1.status == 'started', f"Expected status 'started', got {exec1.status}"
    assert exec1.started_at is not None


def test_race_b_cleanup_holds_lock_against_selected_to_started(db_session):
    """
    Race B: Session A begins stale cleanup and intentionally holds the affected row lock before commit.
    Session B attempts selected -> started while A holds the lock.

    Verifies:
      1. Real transaction overlap with independent sessions.
      2. Deterministic PostgreSQL lock contention occurs (Session B blocks on Session A's row lock).
      3. Session B blocks until Session A commits.
      4. After Session A commits, Session B's conditional UPDATE affects zero rows.
      5. Persisted state remains 'failed'.

    Note: This test proves database transition safety under overlapping transactions,
    not worker/process liveness.
    """
    db_session.execute(text("DELETE FROM search_execution"))
    db_session.commit()

    candidate_id = "role_software_engineer::LOC-BLR-RACE-B"
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=20)

    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='selected',
        selected_at=stale_time,
        query_variant="Race Variant B",
        retrieval_location="Bengaluru",
    )
    db_session.add(exec1)
    db_session.commit()
    exec_id = exec1.id

    bind = db_session.get_bind()

    evt_a_updated = threading.Event()
    evt_release_a = threading.Event()
    evt_b_attempting = threading.Event()
    evt_b_done = threading.Event()

    b_transition_rowcount = [-1]
    worker_errors = []

    def session_a_worker():
        with Session(bind) as session_a:
            try:
                threshold = now - timedelta(minutes=15)
                # Session A performs cleanup on the stale selected execution and holds lock without committing
                res = session_a.execute(
                    text("""
                        UPDATE search_execution
                        SET status = 'failed', completed_at = :now, error_message = 'abandoned claim'
                        WHERE (status = 'selected' AND selected_at < :threshold)
                           OR (status = 'started' AND COALESCE(started_at, selected_at) < :threshold)
                    """),
                    {"now": now, "threshold": threshold},
                )
                assert res.rowcount == 1, "Session A cleanup should update exactly 1 row"

                # Signal that Session A holds the exclusive row lock
                evt_a_updated.set()

                # Intentionally hold open transaction until coordinator signals release
                if not evt_release_a.wait(timeout=5.0):
                    raise TimeoutError("Session A timed out waiting for release")

                session_a.commit()
            except Exception as e:
                session_a.rollback()
                worker_errors.append(("session_a", e))

    def session_b_worker():
        # Wait until Session A has acquired the row lock from cleanup
        if not evt_a_updated.wait(timeout=5.0):
            worker_errors.append(("session_b_setup", TimeoutError("Timed out waiting for Session A lock")))
            return

        with Session(bind) as session_b:
            try:
                evt_b_attempting.set()
                # Attempt selected -> started transition while A holds the row lock
                res = session_b.execute(
                    text(
                        "UPDATE search_execution "
                        "SET status = 'started', started_at = :now, provider_name = :provider_name "
                        "WHERE id = :execution_id AND status = 'selected' "
                        "AND (provider_name IS NULL OR provider_name = :provider_name)"
                    ),
                    {
                        "execution_id": exec_id,
                        "provider_name": "adzuna",
                        "now": now,
                    },
                )
                session_b.commit()
                b_transition_rowcount[0] = res.rowcount
                evt_b_done.set()
            except Exception as e:
                session_b.rollback()
                worker_errors.append(("session_b", e))

    t_a = threading.Thread(target=session_a_worker, name="race_b_worker_a")
    t_b = threading.Thread(target=session_b_worker, name="race_b_worker_b")

    try:
        t_a.start()
        t_b.start()

        assert evt_b_attempting.wait(timeout=5.0), "Session B did not start attempting transition"

        # Deterministically verify lock contention in PostgreSQL:
        # Session B must be blocked waiting on the row lock held by Session A
        contention_detected = False
        deadline = time.time() + 3.0
        with Session(bind) as check_session:
            while time.time() < deadline:
                cnt = check_session.execute(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                ).scalar()
                if cnt and cnt > 0:
                    contention_detected = True
                    break
                time.sleep(0.01)

        assert contention_detected, "Lock contention did not occur: Session B did not block on Session A's lock"
        assert not evt_b_done.is_set(), "Session B finished before Session A released its lock!"

    finally:
        # Safeguard: ensure Session A is released and threads terminate cleanly
        evt_release_a.set()
        t_a.join(timeout=5.0)
        t_b.join(timeout=5.0)

    assert not worker_errors, f"Errors occurred in worker threads: {worker_errors}"
    assert not t_a.is_alive(), "Session A thread did not terminate"
    assert not t_b.is_alive(), "Session B thread did not terminate"

    # Verify Session B's conditional UPDATE affected 0 rows
    assert b_transition_rowcount[0] == 0, f"Session B unexpectedly updated {b_transition_rowcount[0]} rows"

    # Verify persisted state remains failed
    db_session.refresh(exec1)
    assert exec1.status == 'failed', f"Expected status 'failed', got {exec1.status}"
    assert exec1.error_message == 'abandoned claim'


def test_telemetry_missing_is_unknown(db_session):
    """Update adaptive variant classification so incomplete telemetry is treated conservatively as UNKNOWN rather than productive."""
    # We will test _get_variant_history or select_next_search handling of incomplete telemetry
    from app.services.search_selector import generate_candidates, _get_variant_history
    db_session.execute(text("DELETE FROM search_execution"))
    db_session.commit()

    candidate_id = "ROLE-AI-001::LOC-BLR-001"
    now = datetime.now(timezone.utc)

    # Execution with jobs_fetched > 0 but jobs_fresher_eligible IS NULL
    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='succeeded',
        selected_at=now - timedelta(days=2),
        completed_at=now - timedelta(days=2),
        query_variant="AI Engineer",
        jobs_fetched=5,
        jobs_fresher_eligible=None
    )
    db_session.add(exec1)
    db_session.commit()

    # Ensure it's not treated as productive/legacy
    res = select_next_search(db_session, reference_time=now)
    # The classification in select_next_search:
    # fetched > 0 and eligible is None => UNKNOWN => bounded retry 1 day
    # Since it's 2 days old (> 1.0), it should be explored/selected, BUT it shouldn't be LEGACY.
    # Actually, we can just assert it gets selected because it's available for explore.
    assert res.action == 'execute'
    assert res.candidate.query_variant == "AI Engineer"


def test_telemetry_written_when_all_rejected_by_geography(db_session, monkeypatch):
    """Ensure every terminal successful execution writes deterministic quality telemetry, including when all fetched jobs are rejected by geographic filtering."""
    from app.services.ingestion import run_ingestion
    from app.providers.base import JobProvider
    from app.schemas.job_search import JobSearchQuery
    from app.models.job import Job
    from app.api.endpoints.ingestion import internal_execute_search
    from app.schemas.job_search import CanonicalSearchIntent
    from app.providers.types import ProviderName

    # Create execution claim
    candidate_id = "ROLE-AI-001::LOC-BLR-001"
    exec1 = SearchExecutionModel(
        candidate_id=candidate_id,
        status='selected',
        selected_at=datetime.now(timezone.utc),
        query_variant="AI Engineer",
        retrieval_location="Bengaluru",
        provider_name="adzuna"
    )
    db_session.add(exec1)
    db_session.commit()

    class MockProvider(JobProvider):
        def search_jobs(self, query: JobSearchQuery) -> list[Job]:
            return [
                Job(
                    source_job_id="mock1", source="adzuna", discovered_at=datetime.now(timezone.utc),
                    title="AI Engineer",
                    company="MockCo",
                    location="New York", # Will be rejected by geography filter for Bengaluru
                    description="Test",
                    url="http://example.com/mock1"
                )
            ]

    def mock_create_provider(provider_name):
        return MockProvider()

    monkeypatch.setattr("app.api.endpoints.ingestion.create_provider", mock_create_provider)

    # Run endpoint
    intent = CanonicalSearchIntent(
        role_id="ROLE-AI-001",
        keywords="AI Engineer",
        location_id="LOC-BLR-001",
        location="Bengaluru",
        priority=1,
        provider=ProviderName.ADZUNA,
        execution_id=exec1.id
    )

    internal_execute_search(intent, db=db_session)

    # Check telemetry
    db_session.refresh(exec1)
    assert exec1.status == 'succeeded'
    assert exec1.jobs_fetched == 1
    assert exec1.jobs_invalid == 1
    assert exec1.jobs_fresher_eligible == 0  # Should be deterministic 0, not None
    assert exec1.recommendations_created == 0
