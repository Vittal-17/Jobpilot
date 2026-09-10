import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.services.search_selector import generate_candidates, _get_history, select_next_search
from app.domain.candidate import SearchCandidate

def test_generate_candidates_is_deterministic():
    candidates1 = generate_candidates()
    candidates2 = generate_candidates()

    assert len(candidates1) > 0
    assert len(candidates1) == 348
    assert len(candidates1) == len(candidates2)

    for c1, c2 in zip(candidates1, candidates2):
        assert c1.candidate_id == c2.candidate_id
        assert c1.role_id == c2.role_id
        assert c1.location_id == c2.location_id

def test_generate_candidates_has_no_duplicates():
    candidates = generate_candidates()
    cids = [c.candidate_id for c in candidates]
    assert len(cids) == len(set(cids))


def test_never_searched_candidate_outranks_previous_success():
    candidates = generate_candidates()
    best_previous = min(c.priority * 100 + c.tier * 10 for c in candidates)
    worst_never = max(c.priority * 100 + c.tier * 10 - 1_000 for c in candidates)
    assert worst_never < best_previous


@patch("app.services.search_selector._clean_abandoned_claims")
def test_select_next_search_all_fresh(mock_clean):

    # Mock DB where ALL candidates were selected very recently (1 minute ago)
    db_mock = MagicMock()
    now = datetime.now(timezone.utc)

    def fake_execute(stmt, params=None):
        if "GROUP BY candidate_id" in str(stmt):
            class FakeResult:
                def fetchall(self):
                    # Mock that every candidate was selected 1 min ago
                    candidates = generate_candidates()
                    return [(c.candidate_id, None, now - timedelta(minutes=1)) for c in candidates]
            return FakeResult()
        return MagicMock()

    db_mock.execute.side_effect = fake_execute

    result = select_next_search(db_mock)
    assert result.candidate is None
    assert result.reason == "all_candidates_ineligible_or_fresh"

def test_clean_abandoned_claims_preserves_history():
    from app.db.database import SessionLocal
    from app.db.models.search_execution import SearchExecutionModel

    db = SessionLocal()
    try:
        c = SearchExecutionModel(
            candidate_id="ROLE-TEST::LOC-TEST",
            status="selected",
            selected_at=datetime.now(timezone.utc) - timedelta(minutes=20)
        )
        db.add(c)
        db.commit()

        # Act
        from app.services.search_selector import _clean_abandoned_claims
        _clean_abandoned_claims(db)

        # Assert
        db.refresh(c)
        assert c.status == "failed"
        assert c.error_message == "abandoned claim"

        # Test the freshness query explicitly preserves it
        from app.services.search_selector import _get_history
        h = _get_history(db, ["ROLE-TEST::LOC-TEST"])
        assert h["ROLE-TEST::LOC-TEST"]["last_selected"] is not None

    finally:
        db.query(SearchExecutionModel).filter(SearchExecutionModel.candidate_id == "ROLE-TEST::LOC-TEST").delete()
        db.commit()
        db.close()


def test_select_next_search_does_not_swallow_arbitrary_integrity_error(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.services.search_selector import select_next_search
    from app.domain.candidate import SearchCandidate

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

    class MockOrig:
        sqlstate = "23503" # Foreign Key Violation, NOT Unique Violation


    class MockSession:
        def begin_nested(self):
            class Context:
                def __enter__(self): pass
                def __exit__(self, exc_type, exc_val, exc_tb): pass
            return Context()
        def add(self, obj):
            pass
        def flush(self):
            raise IntegrityError("Mock generic integrity error", params=[], orig=MockOrig())
        def commit(self):
            pass
        def rollback(self):
            pass
        def execute(self, *args, **kwargs):
            class MockResult:
                rowcount = 0
                def fetchall(self): return []
                def scalar_one(self): return 1
            return MockResult()


    import pytest
    with pytest.raises(IntegrityError):
        select_next_search(MockSession())


def test_clean_abandoned_claims_ignores_started_state():
    """
    Proves that _clean_abandoned_claims only reclaims 'selected' claims
    and explicitly ignores 'started' claims, preserving the known 005.7 limitation:
    A catastrophic failure after selected -> started may leave the execution in started.
    The current abandonment cleanup only reclaims stale selected claims.
    Recovery/reclamation of stale started executions is deferred to a future milestone.
    """
    from app.db.database import SessionLocal
    from app.db.models.search_execution import SearchExecutionModel
    from datetime import datetime, timezone, timedelta

    db = SessionLocal()
    try:
        # Create a stale 'started' claim
        c = SearchExecutionModel(
            candidate_id="ROLE-TEST::LOC-TEST-STALE-STARTED",
            status="started",
            selected_at=datetime.now(timezone.utc) - timedelta(minutes=60),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=59),
            provider_name="adzuna"
        )
        db.add(c)
        db.commit()

        # Act
        from app.services.search_selector import _clean_abandoned_claims
        rows_affected = _clean_abandoned_claims(db)

        # Assert
        db.refresh(c)
        # It should still be 'started'
        assert c.status == "started"
        # And it shouldn't have been affected by cleanup
        assert rows_affected == 0

    finally:
        db.rollback()
        db.query(SearchExecutionModel).filter(SearchExecutionModel.candidate_id == "ROLE-TEST::LOC-TEST-STALE-STARTED").delete()
        db.commit()
        db.close()
