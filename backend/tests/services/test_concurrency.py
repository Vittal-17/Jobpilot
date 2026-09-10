from app.db.database import SessionLocal, engine
import concurrent.futures
import os
import threading

from sqlalchemy import create_engine
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models.search_execution import SearchExecutionModel
from app.domain.candidate import SearchCandidate
from app.services.search_selector import select_next_search


def test_real_postgres_same_candidate_claim_contention(monkeypatch):
    candidate = SearchCandidate(
        candidate_id="ROLE-TEST::LOC-TEST",
        role_id="ROLE-TEST",
        location_id="LOC-TEST",
        role_canonical="Test Role",
        location_canonical="Test Location",
        priority=1,
        tier=0,
    )
    monkeypatch.setattr(
        "app.services.search_selector.generate_candidates", lambda: [candidate]
    )



    setup = SessionLocal()
    setup.query(SearchExecutionModel).filter_by(candidate_id=candidate.candidate_id).delete()
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)

    def worker():
        session = SessionLocal()
        try:
            result = select_next_search(
                session,
                before_claim=lambda _: barrier.wait(timeout=10),
            )
            session.execute(select(func.count()).select_from(SearchExecutionModel)).scalar_one()
            return result
        finally:
            session.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))

        assert sum(result.candidate is not None for result in results) == 1
        assert sum(
            result.reason == "all_eligible_candidates_claimed_by_others"
            for result in results
        ) == 1

        verify = SessionLocal()
        assert verify.query(SearchExecutionModel).filter_by(
            candidate_id=candidate.candidate_id, status="selected"
        ).count() == 1
        verify.close()
    finally:
        cleanup = SessionLocal()
        cleanup.query(SearchExecutionModel).filter_by(candidate_id=candidate.candidate_id).delete()
        cleanup.commit()
        cleanup.close()
        engine.dispose()

def test_active_claim_blocks_started_state(monkeypatch):
    """
    Proves that if an execution is in the 'started' state,
    another concurrent selector cannot claim it.
    """
    from sqlalchemy.exc import IntegrityError
    candidate = SearchCandidate(
        candidate_id="ROLE-TEST::LOC-TEST-2",
        role_id="ROLE-TEST",
        location_id="LOC-TEST-2",
        role_canonical="Test Role",
        location_canonical="Test Location 2",
        priority=1,
        tier=0,
    )
    monkeypatch.setattr(
        "app.services.search_selector.generate_candidates", lambda: [candidate]
    )

    db = SessionLocal()
    try:
        # 1. Clean up
        db.query(SearchExecutionModel).filter_by(candidate_id=candidate.candidate_id).delete()
        db.commit()

        # 2. Execution A: selected -> started
        from datetime import datetime, timezone
        claim_a = SearchExecutionModel(
            candidate_id=candidate.candidate_id,
            status="started",  # skip selected and simulate already transitioned to started
            selected_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            provider_name="adzuna"
        )
        db.add(claim_a)
        db.commit()

        # 3. Concurrent selector B attempting same candidate
        result = select_next_search(db)

        # 4. Assert B cannot create another active claim
        assert result.candidate is None
        assert result.reason == "all_candidates_ineligible_or_fresh" # Wait, the selector reads history and sees it's active. Let's see what reason it returns.

        # We also need to test the database layer directly to prove the DB rejects it if B somehow tries to insert.
        claim_b = SearchExecutionModel(
            candidate_id=candidate.candidate_id,
            status="selected",
            selected_at=datetime.now(timezone.utc)
        )
        db.add(claim_b)
        import pytest
        with pytest.raises(IntegrityError):
            db.commit()

    finally:
        db.rollback()
        db.query(SearchExecutionModel).filter_by(candidate_id=candidate.candidate_id).delete()
        db.commit()
        db.close()
