from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.services.search_selector import select_next_search, _build_bounded_variants
from app.db.models.search_execution import SearchExecutionModel
from app.domain.taxonomy import ROLE_CATALOG
from app.domain.candidate import SearchCandidate

def test_build_bounded_variants_logic():
    # 1. Fresher-alias selection and deterministic ordering (shortest normalized)
    aliases = ["DevOps Engineer Fresher", "Junior DevOps Engineer", "DevOps Trainee"]
    assert _build_bounded_variants("DevOps Engineer", aliases) == ["DevOps Trainee", "DevOps Engineer"]

    # 2. Broad-alias exclusion
    aliases = ["Backend Software Developer", "Python Backend Developer", "Junior Backend Developer"]
    assert _build_bounded_variants("Backend Developer", aliases) == ["Junior Backend Developer", "Backend Developer"]

    # 3. Normalization and deduplication
    aliases = ["Junior DBA", " junior-dba ", "JUNIOR   DBA", "DBA Fresher"]
    # "junior dba" is length 10, "dba fresher" is length 11. "Junior DBA" comes first.
    assert _build_bounded_variants("Database Administrator", aliases) == ["Junior DBA", "Database Administrator"]

    # 4. No-fresher-alias fallback
    aliases = ["RAG Developer", "Retrieval Augmented Generation Engineer"]
    assert _build_bounded_variants("RAG Engineer", aliases) == ["RAG Engineer"]

def test_variant_cycling_and_duplicate_prevention(db_session: Session):
    role = next(r for r in ROLE_CATALOG if r.id == "ROLE-CL-001")

    # Using the new bounded variants builder directly
    expected_variants = _build_bounded_variants(role.canonical, role.aliases)
    assert len(expected_variants) == 2 # "DevOps Trainee", "DevOps Engineer"

    c = SearchCandidate(
        candidate_id="ROLE-CL-001::LOC-BLR-001",
        role_id=role.id,
        location_id="LOC-BLR-001",
        role_canonical=role.canonical,
        location_canonical="Bengaluru",
        priority=1,
        tier=1,
        variants=expected_variants
    )

    with patch("app.services.search_selector.generate_candidates", return_value=[c]):
        now = datetime.now(timezone.utc)

        # Cycle 1: UNTRIED exploration (should pick the first bounded variant)
        result1 = select_next_search(db_session, reference_time=now)
        assert result1.action == "execute"
        assert result1.candidate.query_variant == expected_variants[1]
        # Complete as PRODUCTIVE but low yield
        exec1 = db_session.query(SearchExecutionModel).filter_by(id=result1.execution_id).one()
        exec1.status = "succeeded"
        exec1.jobs_fetched = 5
        exec1.jobs_fresher_eligible = 1
        exec1.completed_at = now
        db_session.commit()
        # Check cooldown
        assert select_next_search(db_session, reference_time=now).action == "stop"
        # Cycle 2: Untried exploration (should pick the second bounded variant)
        now += timedelta(days=8) # Bypass 7-day cooldown
        result2 = select_next_search(db_session, reference_time=now)
        assert result2.action == "execute"
        assert result2.candidate.query_variant == expected_variants[0]
        # Complete as PRODUCTIVE with high yield
        exec2 = db_session.query(SearchExecutionModel).filter_by(id=result2.execution_id).one()
        exec2.status = "succeeded"
        exec2.jobs_fetched = 20
        exec2.jobs_fresher_eligible = 10
        exec2.completed_at = now
        db_session.commit()

        # Cycle 3: Both tried, both productive. Utility ranking should pick the highest yield!
        # High yield variant is expected_variants[1]
        now += timedelta(days=8)
        result3 = select_next_search(db_session, reference_time=now)
        assert result3.action == "execute"
        assert result3.candidate.query_variant == expected_variants[0]
