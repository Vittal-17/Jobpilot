from datetime import datetime, timezone
from app.schemas.match import RecommendationPreferences
from app.schemas.job import JobResponse
from app.services.matching_service import calculate_match

def test_matching_no_preferences():
    job = JobResponse(
        id=1,
        title="Software Engineer",
        company="TechCorp",
        source="Test",
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences()
    result = calculate_match(job, prefs)
    assert result.score == 50
    assert len(result.reasons) == 1
    assert result.reasons[0].code == "NO_PREFERENCES"

def test_matching_full_match():
    job = JobResponse(
        id=1,
        title="Senior Python Developer",
        company="TechCorp",
        source="Test",
        location="New York",
        remote=True,
        description="Looking for Python, FastAPI and SQL expertise.",
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences(
        preferred_roles="python developer, backend",
        skills="python, fastapi, sql, docker",
        preferred_locations="new york, remote",
        remote_preference="remote",
        experience_years=5
    )
    result = calculate_match(job, prefs)

    # max points:
    # Role: 30 (matches "python developer")
    # Skills: 3 of 4 match -> 30 * 0.75 = 22
    # Location: 20 (matches "new york")
    # Remote: 20 (matches remote)
    # Experience: 20 (>=5 and "senior")
    # Total max = 120. Earned = 30 + 22 + 20 + 20 + 20 = 112. Score = 112/120 * 100 = 93
    assert result.score == 93
    codes = [r.code for r in result.reasons]
    assert "ROLE_MATCH" in codes
    assert "SKILLS_MATCH" in codes
    assert "LOCATION_MATCH" in codes
    assert "REMOTE_MATCH" in codes
    assert "EXPERIENCE_MATCH" in codes

def test_matching_mismatch():
    job = JobResponse(
        id=1,
        title="Junior Frontend Developer",
        company="TechCorp",
        source="Test",
        location="San Francisco",
        remote=False,
        description="Looking for React and CSS.",
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences(
        preferred_roles="backend",
        skills="python, fastapi",
        preferred_locations="new york",
        remote_preference="remote",
        experience_years=5
    )
    result = calculate_match(job, prefs)

    assert result.score == 0
    codes = [r.code for r in result.reasons]
    assert "ROLE_MISMATCH" in codes
    assert "SKILLS_MISMATCH" in codes
    assert "LOCATION_MISMATCH" in codes
    assert "REMOTE_MISMATCH" in codes
    assert "EXPERIENCE_MISMATCH" in codes

def test_matching_partial_missing_job_data():
    job = JobResponse(
        id=1,
        title="Software Engineer",
        company="TechCorp",
        source="Test",
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences(
        remote_preference="remote",
        experience_years=3
    )
    result = calculate_match(job, prefs)

    # max points: Remote(20) + Experience(20) = 40
    # Earned:
    # Remote: job.remote is None -> 10 pts (REMOTE_UNKNOWN)
    # Experience: 3 yrs (mid), title is normal -> 20 pts (EXPERIENCE_MATCH)
    # Total earned = 30 out of 40 -> 75
    assert result.score == 75
    codes = [r.code for r in result.reasons]
    assert "REMOTE_UNKNOWN" in codes
    assert "EXPERIENCE_MATCH" in codes

def test_deterministic_output():
    job = JobResponse(
        id=1,
        title="Software Engineer",
        company="TechCorp",
        source="Test",
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences(preferred_roles="engineer")
    r1 = calculate_match(job, prefs)
    r2 = calculate_match(job, prefs)
    assert r1.score == r2.score
    assert r1.reasons == r2.reasons

def test_token_boundary_matching():
    # Ensures 'java' doesn't match 'javascript'
    job = JobResponse(
        id=1,
        title="Javascript Developer",
        company="TechCorp",
        source="Test",
        description="We write javascript code.",
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences(
        preferred_roles="java developer",
        skills="java",
    )
    result = calculate_match(job, prefs)
    codes = [r.code for r in result.reasons]
    assert "ROLE_MISMATCH" in codes
    assert "SKILLS_MISMATCH" in codes
    assert result.score == 0

def test_remote_unsupported_hybrid():
    # 'hybrid' preference is not fully evaluable against boolean job.remote
    job = JobResponse(
        id=1,
        title="Developer",
        company="Tech",
        source="Test",
        remote=True,
        discovered_at=datetime.now(timezone.utc)
    )
    prefs = RecommendationPreferences(remote_preference="hybrid")
    result = calculate_match(job, prefs)
    assert result.score == 50 # Default score because no other prefs and remote adds 0 max_weight
    codes = [r.code for r in result.reasons]
    assert "REMOTE_UNSUPPORTED" in codes
