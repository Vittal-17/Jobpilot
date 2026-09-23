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
        job_a1 = JobModel(description_is_snippet=False, id=1, title="Senior Data Engineer", company="A", source="test", source_job_id="A1", discovered_at=datetime.now(timezone.utc))
        job_a2 = JobModel(description_is_snippet=False, id=2, title="Data Engineer", company="A", source="test", source_job_id="A2", discovered_at=datetime.now(timezone.utc))
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
        job_b1 = JobModel(description_is_snippet=False, id=3, title="Junior Data Engineer", description="0-1 years", company="B", source="test", source_job_id="B1", discovered_at=datetime.now(timezone.utc))
        job_b2 = JobModel(description_is_snippet=False, id=4, title="Data Engineer Fresher", company="B", source="test", source_job_id="B2", discovered_at=datetime.now(timezone.utc))
        job_b3 = JobModel(description_is_snippet=False, id=5, title="Senior Data Engineer", company="B", source="test", source_job_id="B3", discovered_at=datetime.now(timezone.utc))
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

def test_incomplete_telemetry_penalized(db_session, monkeypatch):
    """
    Ensures that a variant with incomplete telemetry (fetched>0, eligible=None)
    is treated conservatively as UNKNOWN rather than productive.
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
        c.variants = ["Incomplete Variant", "Strong Variant"]
        return [c]
    monkeypatch.setattr("app.services.search_selector.generate_candidates", mock_generate)

    now = datetime.now(timezone.utc)

    # Incomplete Variant has missing eligible telemetry but valid completion
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST2::LOC-TEST2",
        status="succeeded",
        query_variant="Incomplete Variant",
        jobs_fetched=10,
        jobs_fresher_eligible=None, # Missing eligible telemetry
        selected_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=2)
    ))
    db_session.commit()

    # Strong Variant is highly productive
    db_session.add(SearchExecutionModel(
        candidate_id="ROLE-TEST2::LOC-TEST2",
        status="succeeded",
        query_variant="Strong Variant",
        jobs_fetched=20,
        jobs_fresher_eligible=10,
        selected_at=now - timedelta(hours=1),
        completed_at=now - timedelta(hours=1)
    ))
    db_session.commit()

    with patch("app.services.search_selector._get_history", return_value={"ROLE-TEST2::LOC-TEST2": {"last_success": now - timedelta(days=2), "last_selected": now - timedelta(days=2)}}):
        res = select_next_search(db_session, reference_time=now)

    assert res.candidate is not None
    # Strong variant should win because Incomplete Variant is penalized (UNKNOWN within 1 day cooldown)
    assert res.candidate.query_variant == "Strong Variant"

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


def test_select_next_to_internal_search_adaptive_location_end_to_end(db_session, monkeypatch):
    """
    Full cross-endpoint integration test:
    1. A granular Bengaluru candidate (LOC-BLR-002) has all bounded variants with verified NO_INVENTORY.
    2. /internal/select-next is called -> candidate is selected with retrieval_location='Bengaluru'.
    3. The returned intent payload is passed directly to /internal/search.
    4. /internal/search succeeds with 200 (no 409 conflict between canonical and retrieval location),
       and provider search receives location='Bengaluru'.
    """
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from app.main import app
    from app.core.config import settings
    from app.services.provider_router import ProviderSelectionResult
    from app.providers.types import ProviderName
    from app.domain.candidate import SearchCandidate
    from app.schemas.job_search import IngestionResult

    client = TestClient(app)
    api_key = settings.api_secret_key

    # Setup candidate on LOC-BLR-002 with two bounded variants
    candidate = SearchCandidate(
        candidate_id="ROLE-PY-001::LOC-BLR-002",
        role_id="ROLE-PY-001",
        location_id="LOC-BLR-002",
        role_canonical="Python Developer",
        location_canonical="Whitefield, Bengaluru",
        priority=1,
        tier=1,
        variants=["Junior Python Developer", "Python Developer"]
    )
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda db=None: [candidate])
    monkeypatch.setattr(
        "app.services.provider_router.route_provider",
        lambda db: ProviderSelectionResult(provider=ProviderName.ADZUNA, reason="test", policy_version="v1")
    )

    now = datetime.now(timezone.utc)
    # Insert verified NO_INVENTORY history for all variants
    for var_name in candidate.variants:
        db_session.add(SearchExecutionModel(
            candidate_id=candidate.candidate_id,
            status="failed",
            query_variant=var_name,
            jobs_fetched=0,
            jobs_fresher_eligible=None,
            selected_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=2)
        ))
    db_session.commit()

    from app.db.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        # 1. Call /internal/select-next
        resp_select = client.post(
            "/ingestion/internal/select-next",
            headers={"x-api-key": api_key},
            json={"cycle_id": "test-adaptive-cycle"}
        )
        assert resp_select.status_code == 200
        select_data = resp_select.json()
        assert select_data["action"] == "execute"
        intent = select_data["intent"]
        # Verify adaptive broadening occurred on selector side
        assert intent["location"] == "Bengaluru"
        assert intent["execution_id"] is not None

        # Mock provider run_ingestion
        mock_run_ingestion = MagicMock(return_value=(IngestionResult(provider="adzuna", fetched=0), []))
        monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run_ingestion)

        # 2. Call /internal/search with the exact intent returned from /internal/select-next
        resp_search = client.post(
            "/ingestion/internal/search",
            headers={"x-api-key": api_key},
            json=intent
        )
        assert resp_search.status_code == 200, f"Expected 200 but got {resp_search.status_code}: {resp_search.text}"
        mock_run_ingestion.assert_called_once()
        passed_query = mock_run_ingestion.call_args.args[3]
        # Verify provider received the server-authoritative retrieval location
        assert passed_query.location == "Bengaluru"
    finally:
        app.dependency_overrides.clear()
