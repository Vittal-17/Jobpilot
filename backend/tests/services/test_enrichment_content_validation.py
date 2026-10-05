import json
import pytest
from datetime import datetime, timezone
from sqlalchemy import text
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.services.enrichment_worker import EnrichmentWorker, validate_posting_content
from fastapi.testclient import TestClient

client = TestClient(app)


def test_validate_posting_content_heuristics():
    # Valid job postings with various structural sections
    valid_desc_1 = (
        "We are looking for a Junior Python Developer to join our team in Bengaluru.\n"
        "Responsibilities:\n"
        "- Build web applications with FastAPI and PostgreSQL\n"
        "- Write clean and testable code\n"
        "Requirements:\n"
        "- 0-1 years of experience or fresh graduate\n"
        "- Basic knowledge of Python and relational databases\n"
        "How to apply: Submit your resume online."
    )
    is_valid, reason = validate_posting_content("Junior Python Developer", valid_desc_1)
    assert is_valid is True
    assert reason == "validated_posting"

    valid_desc_2 = (
        "About the role: Innovatech is seeking an entry-level software engineer.\n"
        "What you'll do: Design and develop RESTful services using Python.\n"
        "Qualifications: Bachelor's degree in Computer Science, familiar with Git.\n"
        "Perks: Health insurance, flexible hours."
    )
    is_valid, reason = validate_posting_content("Software Engineer", valid_desc_2)
    assert is_valid is True
    assert reason == "validated_posting"

    # Valid short snippet when is_snippet=True
    valid_snippet = "We are hiring a Python Developer in Bengaluru. Freshers welcome to apply."
    is_valid, reason = validate_posting_content("Python Developer", valid_snippet, is_snippet=True)
    assert is_valid is True
    assert reason == "validated_posting"

    # Aggregator titles
    assert validate_posting_content("18340 Python Fresher Job Vacancies In Bangalore", valid_desc_1) == (False, "title_aggregate_count")
    assert validate_posting_content("Python Developer Jobs in Bengaluru (1,000+ Open Roles)", valid_desc_1) == (False, "title_aggregate_count")
    assert validate_posting_content("24 python fresher jobs in Bengaluru", valid_desc_1) == (False, "title_aggregate_count")
    assert validate_posting_content("Hiring Python Fresher jobs in Bengaluru, Karnataka", valid_desc_1) == (False, "title_aggregate_phrase")

    # Aggregator markers in content
    listing_desc_1 = "Showing 45 jobs for Python Developer in Bengaluru. Sort by: relevance."
    assert validate_posting_content("Python Developer", listing_desc_1) == (False, "aggregator_marker")

    listing_desc_2 = "Page 1 of 25. Create job alert for python jobs."
    assert validate_posting_content("Python Developer", listing_desc_2) == (False, "aggregator_marker")

    # Bot and login walls
    assert validate_posting_content("Python Dev", "Just a moment... Enable JavaScript and cookies to continue.") == (False, "bot_or_login_wall")
    assert validate_posting_content("Python Dev", "Please complete the captcha: verify you are human to view.") == (False, "bot_or_login_wall")
    assert validate_posting_content("Python Dev", "Sign in to continue. Please log in to view job details.") == (False, "bot_or_login_wall")

    # Empty and whitespace extractions
    assert validate_posting_content("Python Dev", "") == (False, "empty_content")
    assert validate_posting_content("Python Dev", "   \n\t   ") == (False, "empty_content")
    assert validate_posting_content("Python Dev", "Short", is_snippet=False) == (False, "insufficient_content")

    # Insufficient content (< 150 chars for full page)
    assert validate_posting_content("Python Dev", "Software Engineer position at Acme Corp. Python required.", is_snippet=False) == (False, "insufficient_content")

    # Missing posting signals (long text without responsibilities/qualifications/application instructions)
    non_job_article = (
        "Python is a high-level, general-purpose programming language. Its design philosophy emphasizes code readability "
        "with the use of significant indentation. Python is dynamically typed and garbage-collected. It supports multiple "
        "programming paradigms, including structured, object-oriented and functional programming. Python was conceived in the late 1980s."
    )
    assert validate_posting_content("Python Article", non_job_article, is_snippet=False) == (False, "missing_posting_signals")

    # Repeated job cards: single action marker >= 3
    repeated_desc = (
        "Job 1: Acme Corp. Apply on company site.\n"
        "Job 2: Beta Inc. Apply on company site.\n"
        "Job 3: Gamma Ltd. Apply on company site.\n"
    )
    is_valid, reason = validate_posting_content("Python Developer", repeated_desc)
    assert is_valid is False
    assert "repeated_card_marker" in reason

    # Repeated job cards: multiple action markers sum >= 3
    mixed_actions_desc = (
        "Acme Corp: Quick apply\n"
        "Beta Ltd: Easily apply\n"
        "Gamma Inc: Save job\n"
        "Responsibilities: None listed."
    )
    is_valid, reason = validate_posting_content("Python Developer", mixed_actions_desc)
    assert is_valid is False
    assert "repeated_card_marker" in reason

    # Legitimate single job posting with CTA buttons (top apply and save job)
    single_posting_with_ctas = (
        "Role Overview: Acme is looking for a Backend Engineer in Bengaluru.\n"
        "Save job\n"
        "Responsibilities:\n"
        "- Build scalable microservices using Python and FastAPI\n"
        "- Optimize database queries and background tasks\n"
        "Requirements:\n"
        "- 0-2 years of software engineering experience\n"
        "- Experience with PostgreSQL and Docker\n"
        "Benefits:\n"
        "- Health insurance, remote work options\n"
        "Apply on company site"
    )
    is_valid, reason = validate_posting_content("Backend Engineer", single_posting_with_ctas)
    assert is_valid is True
    assert reason == "validated_posting"


def test_enrichment_worker_rejects_listing_content():
    db = SessionLocal()
    try:
        # Create active user with search
        user = db.query(User).filter(User.email == "test_val_user@example.com").first()
        if not user:
            user = User(email="test_val_user@example.com", password_hash="pw", is_active=True)
            db.add(user)
            db.commit()

        db.execute(text("DELETE FROM user_searches WHERE user_id = :uid"), {"uid": user.id})
        us = UserSearch(user_id=user.id, query="Python Developer", location="Bengaluru", enabled=True)
        db.add(us)

        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, preferred_roles="Python Developer", preferred_locations="Bengaluru", experience_years=0)
            db.add(profile)
        db.commit()

        # Create a job with listing page content
        now = datetime.now(timezone.utc)
        job = JobModel(
            title="Python Developer",
            company="AggregatorCo",
            source="firecrawl",
            source_job_id="fc_test_val_listing",
            discovered_at=now,
            location="Bengaluru",
            description="Initial search snippet",
            description_is_snippet=True,
            url="https://example.com/jobs/listing-123",
            canonical_hash="hash_test_val_listing",
        )
        db.add(job)
        db.commit()

        enrichment = JobEnrichmentModel(
            job_id=job.id,
            status="in_progress",
            url=job.url,
            lease_token="val-token-123",
            lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        db.add(enrichment)
        db.commit()

        worker = EnrichmentWorker()
        listing_scraped_text = (
            "Showing 180 jobs for Python in Bengaluru. Page 1 of 18.\n"
            "Sort by: relevance. Create job alert.\n"
            "Apply on company site.\nApply on company site.\nApply on company site."
        )

        worker.complete_success(db, job.id, "val-token-123", listing_scraped_text)

        # Verify job and enrichment state
        db_job = db.query(JobModel).filter(JobModel.id == job.id).first()
        db_enrich = db.query(JobEnrichmentModel).filter(JobEnrichmentModel.job_id == job.id).first()

        # Job snippet flag must remain True (not updated with listing text)
        assert db_job.description_is_snippet is True
        assert db_job.description == "Initial search snippet"

        # Enrichment status must be 'unsupported' with rejected_listing reason
        assert db_enrich.status == "unsupported"
        assert "rejected_listing_content" in (db_enrich.error_reason or "")
        assert db_enrich.result_telemetry is not None
        assert db_enrich.result_telemetry.get("validation") == "rejected_listing"

        # No recommendations created for this listing
        recs = db.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.job_id == job.id).all()
        assert len(recs) == 0

    finally:
        db.execute(text("DELETE FROM recommendation_history WHERE job_id IN (SELECT id FROM jobs WHERE source_job_id = 'fc_test_val_listing')"))
        db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%listing-123%'"))
        db.execute(text("DELETE FROM jobs WHERE source_job_id = 'fc_test_val_listing'"))
        db.execute(text("DELETE FROM user_searches WHERE query = 'Python Developer' AND user_id IN (SELECT id FROM users WHERE email = 'test_val_user@example.com')"))
        db.commit()
        db.close()


def test_enrichment_worker_accepts_valid_posting():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test_val_user2@example.com").first()
        if not user:
            user = User(email="test_val_user2@example.com", password_hash="pw", is_active=True)
            db.add(user)
            db.commit()

        db.execute(text("DELETE FROM user_searches WHERE user_id = :uid"), {"uid": user.id})
        us = UserSearch(user_id=user.id, query="Python Developer", location="Bengaluru", enabled=True)
        db.add(us)

        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, preferred_roles="Python Developer", preferred_locations="Bengaluru", experience_years=0)
            db.add(profile)
        db.commit()

        now = datetime.now(timezone.utc)
        job = JobModel(
            title="Junior Python Developer",
            company="Innovatech",
            source="firecrawl",
            source_job_id="fc_test_val_valid",
            discovered_at=now,
            location="Bengaluru",
            description="Initial search snippet",
            description_is_snippet=True,
            url="https://innovatech.com/careers/jr-py-dev",
            canonical_hash="hash_test_val_valid",
        )
        db.add(job)
        db.commit()

        enrichment = JobEnrichmentModel(
            job_id=job.id,
            status="in_progress",
            url=job.url,
            lease_token="val-token-456",
            lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        db.add(enrichment)
        db.commit()

        worker = EnrichmentWorker()
        genuine_job_desc = (
            "About Innovatech: We are hiring a Junior Python Developer in Bengaluru.\n"
            "Responsibilities: Build scalable REST APIs with FastAPI and PostgreSQL.\n"
            "Requirements: Strong knowledge of Python, familiarity with relational databases.\n"
            "Fresher friendly role with mentor guidance."
        )

        worker.complete_success(db, job.id, "val-token-456", genuine_job_desc)

        db_job = db.query(JobModel).filter(JobModel.id == job.id).first()
        db_enrich = db.query(JobEnrichmentModel).filter(JobEnrichmentModel.job_id == job.id).first()

        # Job description updated and snippet flag cleared
        assert db_job.description_is_snippet is False
        assert db_job.description == genuine_job_desc

        # Enrichment status is success
        assert db_enrich.status == "success"
        assert db_enrich.error_reason is None
        assert db_enrich.result_telemetry is not None
        assert db_enrich.result_telemetry.get("validation") == "validated_posting"

        # Recommendation should be created for the active user
        recs = db.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.job_id == job.id).all()
        assert len(recs) == 1
        assert recs[0].user_id == user.id
        assert recs[0].score >= 50

    finally:
        db.execute(text("DELETE FROM recommendation_history WHERE job_id IN (SELECT id FROM jobs WHERE source_job_id = 'fc_test_val_valid')"))
        db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%jr-py-dev%'"))
        db.execute(text("DELETE FROM jobs WHERE source_job_id = 'fc_test_val_valid'"))
        db.execute(text("DELETE FROM user_searches WHERE query = 'Python Developer' AND user_id IN (SELECT id FROM users WHERE email = 'test_val_user2@example.com')"))
        db.commit()
        db.close()


def test_snippet_fallback_follows_same_validation_guard():
    """
    Prove snippet fallback follows the exact same validation guard:
    - Snippet with aggregator/listing text is rejected (status='unsupported', no recs).
    - Snippet with genuine posting text is accepted into evaluation.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test_val_snippet_user@example.com").first()
        if not user:
            user = User(email="test_val_snippet_user@example.com", password_hash="pw", is_active=True)
            db.add(user)
            db.commit()

        db.execute(text("DELETE FROM user_searches WHERE user_id = :uid"), {"uid": user.id})
        us = UserSearch(user_id=user.id, query="Python Developer", location="Bengaluru", enabled=True)
        db.add(us)
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, preferred_roles="Python Developer", preferred_locations="Bengaluru", experience_years=0)
            db.add(profile)
        db.commit()

        worker = EnrichmentWorker()
        now = datetime.now(timezone.utc)

        # Case A: Fallback snippet is aggregator/listing text
        job_listing = JobModel(
            title="Python Developer",
            company="AggregatorSnippetCo",
            source="firecrawl",
            source_job_id="fc_test_val_snip_listing",
            discovered_at=now,
            location="Bengaluru",
            description="Showing 150 jobs for Python in Bengaluru. Page 1 of 15.",
            description_is_snippet=True,
            url="https://aggregator.com/jobs/snip-listing",
            canonical_hash="hash_val_snip_listing",
        )
        db.add(job_listing)
        db.commit()

        enrich_listing = JobEnrichmentModel(
            job_id=job_listing.id,
            status="in_progress",
            url=job_listing.url,
            lease_token="snip-token-1",
            lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        db.add(enrich_listing)
        db.commit()

        worker.complete_with_snippet(db, job_listing.id, "snip-token-1", "HTTP 500 error", unsupported=True)

        db_enrich_listing = db.query(JobEnrichmentModel).filter(JobEnrichmentModel.job_id == job_listing.id).first()
        assert db_enrich_listing.status == "unsupported"
        assert "rejected_listing_content" in (db_enrich_listing.error_reason or "")
        assert db_enrich_listing.result_telemetry.get("validation") == "rejected_listing"

        recs_listing = db.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.job_id == job_listing.id).all()
        assert len(recs_listing) == 0

        # Case B: Fallback snippet is a genuine job snippet
        job_valid = JobModel(
            title="Junior Python Developer",
            company="GenuineSnippetCo",
            source="firecrawl",
            source_job_id="fc_test_val_snip_valid",
            discovered_at=now,
            location="Bengaluru",
            description="We are hiring a Junior Python Developer in Bengaluru. Freshers welcome to apply.",
            description_is_snippet=True,
            url="https://genuinesnip.com/jobs/snip-valid",
            canonical_hash="hash_val_snip_valid",
        )
        db.add(job_valid)
        db.commit()

        enrich_valid = JobEnrichmentModel(
            job_id=job_valid.id,
            status="in_progress",
            url=job_valid.url,
            lease_token="snip-token-2",
            lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        db.add(enrich_valid)
        db.commit()

        worker.complete_with_snippet(db, job_valid.id, "snip-token-2", "Native scrape blocked", unsupported=True)

        db_enrich_valid = db.query(JobEnrichmentModel).filter(JobEnrichmentModel.job_id == job_valid.id).first()
        assert db_enrich_valid.status == "unsupported"
        assert db_enrich_valid.result_telemetry.get("validation") == "snippet_fallback"

    finally:
        db.execute(text("DELETE FROM recommendation_history WHERE job_id IN (SELECT id FROM jobs WHERE source_job_id LIKE 'fc_test_val_snip_%')"))
        db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%snip-listing%' OR url LIKE '%snip-valid%'"))
        db.execute(text("DELETE FROM jobs WHERE source_job_id LIKE 'fc_test_val_snip_%'"))
        db.execute(text("DELETE FROM user_searches WHERE user_id IN (SELECT id FROM users WHERE email = 'test_val_snippet_user@example.com')"))
        db.commit()
        db.close()


def test_listing_content_cannot_reach_eligibility_recs_notifications():
    """
    Prove listing content cannot reach eligibility, recommendations, or notifications.
    """
    db = SessionLocal()
    headers = {"X-API-Key": settings.api_secret_key}
    try:
        user = db.query(User).filter(User.email == "test_val_norec_user@example.com").first()
        if not user:
            user = User(email="test_val_norec_user@example.com", password_hash="pw", is_active=True)
            db.add(user)
            db.commit()

        db.execute(text("DELETE FROM user_searches WHERE user_id = :uid"), {"uid": user.id})
        us = UserSearch(user_id=user.id, query="Python Developer", location="Bengaluru", enabled=True)
        db.add(us)
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, preferred_roles="Python Developer", preferred_locations="Bengaluru", experience_years=0)
            db.add(profile)
        db.commit()

        worker = EnrichmentWorker()
        now = datetime.now(timezone.utc)
        job = JobModel(
            title="Python Developer",
            company="AggregatorSpam",
            source="firecrawl",
            source_job_id="fc_test_val_norec",
            discovered_at=now,
            location="Bengaluru",
            description="Initial search snippet",
            description_is_snippet=True,
            url="https://aggregator.com/jobs/listing-spam",
            canonical_hash="hash_val_norec",
        )
        db.add(job)
        db.commit()

        enrichment = JobEnrichmentModel(
            job_id=job.id,
            status="in_progress",
            url=job.url,
            lease_token="norec-token",
            lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        db.add(enrichment)
        db.commit()

        # Scraped text is an aggregator listing
        listing_text = (
            "Showing 120 Python Developer jobs in Bengaluru. Page 1 of 12.\n"
            "Sort by: relevance. Create job alert.\n"
            "Apply on company site.\nApply on company site.\nApply on company site."
        )
        worker.complete_success(db, job.id, "norec-token", listing_text)

        # 1. Verify job is NOT modified to full description
        db_job = db.query(JobModel).filter(JobModel.id == job.id).first()
        assert db_job.description_is_snippet is True

        # 2. Verify zero recommendations exist
        recs = db.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.job_id == job.id).all()
        assert len(recs) == 0

        # 3. Verify notification claim returns nothing for this job
        claim_resp = client.post(
            "/ingestion/internal/notifications/claim",
            headers=headers,
            json={"user_id": user.id, "delivery_id": "test_deliv_norec_1", "limit": 5}
        )
        if claim_resp.status_code == 200:
            claimed_jobs = claim_resp.json().get("recommendations", [])
            assert not any(j["job_id"] == job.id for j in claimed_jobs)

    finally:
        db.execute(text("DELETE FROM notification_deliveries WHERE delivery_id = 'test_deliv_norec_1'"))
        db.execute(text("DELETE FROM recommendation_history WHERE job_id IN (SELECT id FROM jobs WHERE source_job_id = 'fc_test_val_norec')"))
        db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%listing-spam%'"))
        db.execute(text("DELETE FROM jobs WHERE source_job_id = 'fc_test_val_norec'"))
        db.execute(text("DELETE FROM user_searches WHERE user_id IN (SELECT id FROM users WHERE email = 'test_val_norec_user@example.com')"))
        db.commit()
        db.close()
