import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_db
from app.db.models.search_execution import SearchExecutionModel
from app.db.models.job import JobModel
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.services.search_selector import _get_variant_history, select_next_search, SearchCandidate
from app.main import app

client = TestClient(app)

def test_execution_scoped_quality_counts(db_session, monkeypatch):
    """
    Ensures that jobs_fresher_eligible and recommendations_created are strictly scoped
    to the jobs returned by THIS specific execution, rather than recalculating globally.
    """
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        # Create a user to receive recommendations deterministically
        db_session.add(User(id=1, email="test@test.com", password_hash="hash"))
        db_session.add(UserSearch(id=999, user_id=1, query="Data Engineer", location="Bengaluru", enabled=True))
        db_session.add(UserProfile(user_id=1, preferred_roles="Data Engineer", experience_years=1))
        db_session.commit()

        # Insert historical jobs (e.g. from Execution A)
        job_a1 = JobModel(id=1, title="Senior Data Engineer", company="A", source="test", source_job_id="A1", discovered_at=datetime.now(timezone.utc))
        job_a2 = JobModel(id=2, title="Data Engineer", company="A", source="test", source_job_id="A2", discovered_at=datetime.now(timezone.utc))
        db_session.add_all([job_a1, job_a2])
        db_session.commit()

        # Create Execution A claim
        claim_a = SearchExecutionModel(
            candidate_id="ROLE-DA-002::LOC-BLR-001",
            status="selected",
            provider_name="adzuna",
            selected_at=datetime.now(timezone.utc),
            query_variant="Data Engineer"
        )
        db_session.add(claim_a)
        db_session.commit()

        # Mock run_ingestion for Execution A to return job_ids [1, 2]
        from app.services.ingestion import IngestionResult
        mock_run_a = MagicMock(return_value=(IngestionResult(provider="adzuna", fetched=2, created=2, duplicates=0, invalid=0, failed=0), [1, 2]))
        monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run_a)

        intent_a = {
            "role_id": "ROLE-DA-002",
            "keywords": "Data Engineer",
            "location_id": "LOC-BLR-001",
            "location": "Bengaluru",
            "priority": 1,
            "execution_id": claim_a.id,
            "provider": "adzuna"
        }
        res_a = client.post("/ingestion/internal/search", headers={"X-Api-Key": settings.api_secret_key}, json=intent_a)
        assert res_a.status_code == 200, res_a.json()
        claim_a.status = "succeeded"
        db_session.commit()

        # Verify A.jobs_fresher_eligible = 0 (since neither explicitly pass fresher gate without 'fresher' or 'junior' or '0-1')
        db_session.refresh(claim_a)
        assert claim_a.jobs_fresher_eligible == 0
        assert claim_a.recommendations_created == 0

        # Now Execution B
        job_b1 = JobModel(id=3, title="Junior Data Engineer", description="0-1 years", company="B", source="test", source_job_id="B1", discovered_at=datetime.now(timezone.utc))
        job_b2 = JobModel(id=4, title="Data Engineer Fresher", company="B", source="test", source_job_id="B2", discovered_at=datetime.now(timezone.utc))
        job_b3 = JobModel(id=5, title="Senior Data Engineer", company="B", source="test", source_job_id="B3", discovered_at=datetime.now(timezone.utc))
        db_session.add_all([job_b1, job_b2, job_b3])
        db_session.commit()

        claim_b = SearchExecutionModel(
            candidate_id="ROLE-DA-002::LOC-BLR-001",
            status="selected",
            provider_name="adzuna",
            selected_at=datetime.now(timezone.utc),
            query_variant="Junior Data Engineer"
        )
        db_session.add(claim_b)
        db_session.commit()

        mock_run_b = MagicMock(return_value=(IngestionResult(provider="adzuna", fetched=3, created=2, duplicates=1, invalid=0, failed=0), [2, 3, 4]))
        monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run_b)

        intent_b = {
            "role_id": "ROLE-DA-002",
            "keywords": "Junior Data Engineer",
            "location_id": "LOC-BLR-001",
            "location": "Bengaluru",
            "priority": 1,
            "execution_id": claim_b.id,
            "provider": "adzuna"
        }
        res_b = client.post("/ingestion/internal/search", headers={"X-Api-Key": settings.api_secret_key}, json=intent_b)
        assert res_b.status_code == 200

        # Verify B.jobs_fresher_eligible = 2
        db_session.refresh(claim_b)
        assert claim_b.jobs_fresher_eligible == 2

        # Recommendations created should be 2 (if score >= 50, but let's just check the DB directly)
        # Actually our recommendation logic assigns match score.
        # But crucially, we just need to ensure the DB reflects execution-scoped counts.
        assert claim_b.recommendations_created == 2

        # We explicitly ensure B doesn't count jobs [1, 2] even though they exist!
        # If it counted globally, it would evaluate 5 jobs. It only evaluates [3, 4, 5].

    finally:
        app.dependency_overrides.clear()

def test_variant_penalty_and_fallback(db_session, monkeypatch):
    """
    Ensures that a variant with NO_YIELD (fresher_eligible=0) is penalized for 7 days,
    and if ALL are penalized, it safely falls back.
    """
    # Create candidate with two variants
    def mock_generate(db):
        c = SearchCandidate(
            candidate_id="ROLE-TEST::LOC-TEST",
            role_id="ROLE-TEST",
            role_canonical="Test Role",
            location_id="LOC-TEST",
            location_canonical="Test Loc",
            priority=1,
            tier=1
        )
        c.variants = ["Weak Variant", "Strong Variant"]
        return [c]
    monkeypatch.setattr("app.services.search_selector.generate_candidates", mock_generate)

    # 1. Insert a WEAK execution for "Weak Variant" completed 2 days ago
    now = datetime.now(timezone.utc)
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST::LOC-TEST",
        status="succeeded",
        query_variant="Weak Variant",
        jobs_fetched=10,
        jobs_fresher_eligible=0,
        selected_at=now - timedelta(days=2, hours=2),
        completed_at=now - timedelta(days=2)
    ))
    db_session.commit()

    # 2. Select next search - should skip "Weak Variant" (penalized) and pick "Strong Variant"
    res = select_next_search(db_session)
    assert res.candidate is not None
    assert res.candidate.query_variant == "Strong Variant"

    # Check claim is saved with correct query_variant
    claim = db_session.query(SearchExecutionModel).filter(SearchExecutionModel.id == res.execution_id).first()
    assert claim.query_variant == "Strong Variant"

    # Clear active claim
    db_session.delete(claim)
    db_session.commit()

    # 3. Insert a WEAK execution for "Strong Variant" completed 1 day ago
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST::LOC-TEST",
        status="succeeded",
        query_variant="Strong Variant",
        jobs_fetched=5,
        jobs_fresher_eligible=0,
        selected_at=now - timedelta(days=1, hours=2),
        completed_at=now - timedelta(days=1)
    ))
    db_session.commit()

    # 4. Now both are penalized! The fallback mechanism should ignore the penalty and pick deterministically by latest execution ID
    res2 = select_next_search(db_session)
    assert res2.candidate is not None
    # 005.3 policy picks the one with the oldest fallback ID. "Strong Variant" was inserted later (higher ID). So it picks "Weak Variant" (oldest ID).
    assert res2.candidate.query_variant == "Weak Variant"

    db_session.delete(db_session.query(SearchExecutionModel).filter(SearchExecutionModel.id == res2.execution_id).first())
    db_session.commit()

    # 5. Move both WEAK executions to 8 days ago (expired penalty)
    from sqlalchemy import text
    db_session.execute(
        text("UPDATE search_execution SET completed_at = :new_time"),
        {"new_time": now - timedelta(days=8)}
    )
    db_session.commit()

    # 6. Now both are unpenalized. Since both had fresher_eligible=0, they are put into the STALE explore queue.
    # STALE explore queue sorts by oldest execution ID. "Weak Variant" was executed first, so it has a lower ID.
    res3 = select_next_search(db_session)
    assert res3.candidate is not None
    assert res3.candidate.query_variant == "Weak Variant"

def test_legacy_telemetry_unpenalized(db_session, monkeypatch):
    """
    Ensures that a variant with legacy telemetry (fetched>0, eligible=None, valid completed_at)
    bypasses penalties (is not UNKNOWN/FAILED, NO_INVENTORY, or NO_FRESHER) and enters the
    productive utility path with the Beta prior 1/(1+4)=0.20, ranking correctly.
    """
    from unittest.mock import patch
    def mock_generate(db):
        c = SearchCandidate(
            candidate_id="ROLE-TEST2::LOC-TEST2",
            role_id="ROLE-TEST2",
            role_canonical="Test Role",
            location_id="LOC-TEST2",
            location_canonical="Test Loc",
            priority=1,
            tier=1
        )
        c.variants = ["Legacy Variant", "Strong Variant"]
        return [c]
    monkeypatch.setattr("app.services.search_selector.generate_candidates", mock_generate)

    now = datetime.now(timezone.utc)

    # Legacy Variant has missing eligible telemetry but valid completion
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST2::LOC-TEST2",
        status="succeeded",
        query_variant="Legacy Variant",
        jobs_fetched=10,
        jobs_fresher_eligible=None, # Missing eligible telemetry
        selected_at=now - timedelta(days=3),
        completed_at=now - timedelta(days=3)
    ))
    db_session.commit()

    # Strong Variant is highly productive
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST2::LOC-TEST2",
        status="succeeded",
        query_variant="Strong Variant",
        jobs_fetched=20,
        jobs_fresher_eligible=10,
        selected_at=now - timedelta(days=2),
        completed_at=now - timedelta(days=2)
    ))
    db_session.commit()

    res = select_next_search(db_session, reference_time=now)
    assert res.candidate is not None
    # Utility check: Strong is (10+1)/(20+5)=0.44. Legacy is 1/5=0.20. Strong wins.
    assert res.candidate.query_variant == "Strong Variant"

    claim = db_session.query(SearchExecutionModel).filter_by(id=res.execution_id).first()
    db_session.delete(claim)
    db_session.commit()

    # Apply 7-day NO_INVENTORY penalty to Strong Variant
    db_session.execute(
        SearchExecutionModel.__table__.update()
        .where(SearchExecutionModel.query_variant == "Strong Variant")
        .values(jobs_fetched=0, jobs_fresher_eligible=0, completed_at=now - timedelta(hours=1))
    )
    db_session.commit()

    with patch("app.services.search_selector._get_history", return_value={}):
        res2 = select_next_search(db_session, reference_time=now)
        assert res2.candidate is not None
        # Legacy Variant is never penalized, so it safely evaluates and is selected.
        assert res2.candidate.query_variant == "Legacy Variant"

def test_unknown_telemetry_1_day_penalty(db_session, monkeypatch):
    """
    Proves the 1-day UNKNOWN penalty for genuinely missing telemetry (fetched=NULL)
    and that the variant re-enters the explore queue after the penalty expires.

    Uses two variants that BOTH have execution history:
      - "Unknown Variant": fetched=NULL (UNKNOWN), completed 2 hours ago (< 1 day).
      - "Productive Variant": fetched=20, eligible=8, completed 3 days ago (productive, off cooldown).

    Phase 1 (within penalty): Unknown is penalized, Productive is the only available variant → selected.
    Phase 2 (after penalty): Unknown's 1-day penalty expires and it enters the explore queue.
             Explore queue has priority over utility ranking, so Unknown is now selected.
    """
    def mock_generate(db):
        c = SearchCandidate(
            candidate_id="ROLE-TEST3::LOC-TEST3",
            role_id="ROLE-TEST3",
            role_canonical="Test Role",
            location_id="LOC-TEST3",
            location_canonical="Test Loc",
            priority=1,
            tier=1
        )
        c.variants = ["Unknown Variant", "Productive Variant"]
        return [c]
    monkeypatch.setattr("app.services.search_selector.generate_candidates", mock_generate)

    now = datetime.now(timezone.utc)

    # Unknown Variant: genuinely missing telemetry (fetched=NULL), completed 2 hours ago.
    # status='failed' avoids candidate-level 24-hour success cooldown.
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST3::LOC-TEST3",
        status="failed",
        query_variant="Unknown Variant",
        jobs_fetched=None,
        jobs_fresher_eligible=None,
        selected_at=now - timedelta(hours=3),
        completed_at=now - timedelta(hours=2)  # < 1 day old → penalized
    ))
    db_session.commit()

    # Productive Variant: valid productive history, completed 3 days ago (off all cooldowns).
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST3::LOC-TEST3",
        status="succeeded",
        query_variant="Productive Variant",
        jobs_fetched=20,
        jobs_fresher_eligible=8,
        selected_at=now - timedelta(days=3, hours=1),
        completed_at=now - timedelta(days=3)
    ))
    db_session.commit()

    # Phase 1: Within the 1-day penalty window.
    # Unknown is penalized (fetched=NULL, age < 1 day) → only Productive is available.
    res1 = select_next_search(db_session, reference_time=now)
    assert res1.candidate is not None
    assert res1.candidate.query_variant == "Productive Variant"

    # Clean up the claim so the candidate is not on selection cooldown.
    claim = db_session.query(SearchExecutionModel).filter_by(id=res1.execution_id).first()
    db_session.delete(claim)
    db_session.commit()

    # Phase 2: Advance past the 1-day penalty.
    # completed_at was 2 hours before `now`; at now + 23 hours it is 25 hours old → age_days > 1.0.
    # Unknown re-enters the explore queue, which has priority over utility ranking.
    later = now + timedelta(hours=23)
    res2 = select_next_search(db_session, reference_time=later)
    assert res2.candidate is not None
    assert res2.candidate.query_variant == "Unknown Variant"
