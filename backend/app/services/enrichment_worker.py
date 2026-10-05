import uuid
import os
import socket
import logging
import httpx
from datetime import datetime, timezone
from typing import List
import sqlalchemy
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.db.database import SessionLocal
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.services.eligibility import is_fresher_eligible
from app.services.matching_service import calculate_match
from app.schemas.match import RecommendationPreferences
from app.schemas.job import JobResponse
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user_profile import UserProfile
from app.db.models.user_search import UserSearch
import json
import re
from app.services.scraper.ssrf import SSRFClient, SSRFViolation
from app.services.scraper.extractor import extract_job_description, ExtractionError

logger = logging.getLogger(__name__)

CONTENT_AGGREGATE_PATTERNS = [
    re.compile(r'\b(?:showing\s+)?\d+[\d,+]*(?:\+)?\s*(?:python|backend|frontend|software|developer|engineer|fresher|entry\s*level)?\s*(?:jobs?|vacancies|openings|roles)\s*(?:found|available)?\b', re.IGNORECASE),
    re.compile(r'\bpage\s+\d+\s+of\s+\d+\b', re.IGNORECASE),
    re.compile(r'\bsort\s+by\s*:\s*(?:relevance|date)\b', re.IGNORECASE),
    re.compile(r'\bshowing\s+jobs\s+for\b', re.IGNORECASE),
    re.compile(r'\bcreate\s+job\s+alert\b', re.IGNORECASE),
    re.compile(r'\bget\s+new\s+jobs\s+by\s+email\b', re.IGNORECASE),
    re.compile(r'\b(?:find|search)\s+more\s+jobs\b', re.IGNORECASE),
]

BOT_OR_LOGIN_WALL_PATTERNS = [
    re.compile(r'\b(?:please\s+enable\s+cookies|checking\s+your\s+browser|enable\s+javascript|just\s+a\s+moment\b|access\s+denied|verify\s+you\s+are\s+human|security\s+check|complete\s+the\s+captcha)\b', re.IGNORECASE),
    re.compile(r'\b(?:please\s+log\s+in\s+to\s+view|sign\s+in\s+to\s+(?:continue|view|apply)|log\s+in\s+to\s+see\s+more|create\s+an\s+account\s+to\s+view)\b', re.IGNORECASE),
]

POSTING_POSITIVE_SIGNALS = [
    re.compile(r'\b(?:responsibilities|duties|what\s+you(?:[\'’]ll|\s+will)\s+do|the\s+role|role\s+overview|job\s+summary|about\s+the\s+role|key\s+responsibilities|day[- ]to[- ]day)\b', re.IGNORECASE),
    re.compile(r'\b(?:requirements|qualifications|what\s+we(?:[\'’]re|\s+are)\s+looking\s+for|skills\s+required|candidate\s+profile|eligibility|who\s+you\s+are|minimum\s+qualifications|preferred\s+qualifications|basic\s+qualifications)\b', re.IGNORECASE),
    re.compile(r'\b(?:how\s+to\s+apply|apply\s+now|submit\s+your\s+(?:resume|application)|to\s+apply|compensation|benefits|about\s+us|perks|we\s+offer)\b', re.IGNORECASE),
]

CARD_ACTION_MARKERS = [
    "apply on company site",
    "quick apply",
    "easily apply",
    "easy apply",
    "save job",
    "view job details",
]


def validate_posting_content(title: str | None, text: str | None, is_snippet: bool = False) -> tuple[bool, str]:
    """
    Validate whether the enriched job description or snippet represents a genuine
    individual job posting rather than a multi-job aggregator, listing page, bot wall,
    or login screen. Uses a composite multi-signal approach combining positive posting
    structural markers with negative listing/bot/aggregator markers.
    """
    from app.services.job_url_classifier import TITLE_AGGREGATE_COUNT_RE, TITLE_AGGREGATE_PHRASE_RE

    # 1. Title heuristics (universal across all job pages)
    if title and isinstance(title, str) and title.strip():
        clean_title = title.strip()
        if TITLE_AGGREGATE_COUNT_RE.search(clean_title):
            return False, "title_aggregate_count"
        if TITLE_AGGREGATE_PHRASE_RE.search(clean_title):
            return False, "title_aggregate_phrase"

    if not text or not isinstance(text, str) or not text.strip():
        return False, "empty_content"

    clean_text = text.strip()
    lower_text = clean_text.lower()

    # 2. Bot / Anti-bot / Login wall detection
    for pattern in BOT_OR_LOGIN_WALL_PATTERNS:
        if pattern.search(lower_text):
            return False, "bot_or_login_wall"

    # 3. Aggregator & pagination markers in content
    for pattern in CONTENT_AGGREGATE_PATTERNS:
        if pattern.search(clean_text):
            return False, "aggregator_marker"

    # 4. Repeated job-card action markers
    total_actions = 0
    for marker in CARD_ACTION_MARKERS:
        cnt = lower_text.count(marker)
        if cnt >= 3:
            return False, f"repeated_card_marker:{marker}"
        total_actions += cnt

    if total_actions >= 3:
        return False, "repeated_card_marker:multiple_actions"

    # 5. Length and positive structural posting signals
    if not is_snippet:
        if len(clean_text) < 150:
            return False, "insufficient_content"
        has_positive_signal = (
            any(p.search(clean_text) for p in POSTING_POSITIVE_SIGNALS)
            or (title and any(tok in title.lower() for tok in (
                "engineer", "developer", "manager", "intern", "architect",
                "analyst", "specialist", "designer", "consultant", "scientist",
                "lead", "officer", "associate", "trainee", "fresher", "programmer", "administrator"
            )))
        )
        if not has_positive_signal:
            return False, "missing_posting_signals"

    return True, "validated_posting"

class EnrichmentWorker:
    """
    Background worker that claims pending job enrichment tasks and scrapes descriptions.
    Enforces a two-tier extraction pipeline:
      Tier 1: Direct native fetch via SSRF-safe client.
      Tier 2: Firecrawl Scrape fallback when native fetch is blocked or anti-botted.
      Terminal Fallback: Keep snippet if Firecrawl is exhausted, disabled, or capped.

    Each invocation of `claim_and_process(db)` represents a canonical batch execution unit.
    The per-run credit cap (`firecrawl_enrichment_max_credits_per_run`, default 10) is
    enforced strictly per execution unit (batch), resetting at the start of each batch claim.
    Overall monthly consumption remains authoritatively capped by the 900-credit ceiling
    in `provider_usage`.
    """
    def __init__(self, max_attempts=3, claim_batch_size=10, worker_id: str | None = None):
        self.max_attempts = max_attempts
        self.claim_batch_size = claim_batch_size
        self.worker_id = worker_id or os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
        self.ssrf_client = SSRFClient()
        self.credits_used_this_run = 0

    def run(self):
        with SessionLocal() as db:
            self.claim_and_process(db)

    def claim_and_process(self, db: Session):
        """
        Canonical batch execution unit for the enrichment worker.
        Claims a batch of up to `claim_batch_size` pending jobs and processes them.
        Resets `self.credits_used_this_run = 0` at the start of each execution unit,
        authoritatively capping Firecrawl credits used within this batch to
        `settings.firecrawl_enrichment_max_credits_per_run`.
        """
        self.credits_used_this_run = 0
        # Terminalize expired tasks that exceeded max attempts
        db.execute(text('''
            UPDATE job_enrichments SET
                status = 'failure',
                error_reason = 'Max attempts exceeded due to worker crashes',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'in_progress'
              AND lease_expires_at <= clock_timestamp()
              AND attempts + 1 >= :max_attempts;
        '''), {"max_attempts": self.max_attempts})

        claim_query = text('''
            WITH claimed AS (
                SELECT job_id, status FROM job_enrichments
                WHERE status = 'pending'
                   OR (status = 'in_progress' AND lease_expires_at <= clock_timestamp() AND attempts + 1 < :max_attempts)
                   OR (status = 'retry' AND attempts < :max_attempts)
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            UPDATE job_enrichments e SET
                status = 'in_progress',
                lease_holder = :worker_id,
                lease_token = :new_token,
                lease_expires_at = clock_timestamp() + INTERVAL '5 minutes',
                attempts = CASE WHEN e.status = 'in_progress' THEN e.attempts + 1 ELSE e.attempts END
            FROM claimed c
            WHERE e.job_id = c.job_id
            RETURNING e.job_id, e.url, e.source_execution_id;
        ''')

        new_token = str(uuid.uuid4())

        try:
            result = db.execute(claim_query, {
                "worker_id": self.worker_id,
                "new_token": new_token,
                "max_attempts": self.max_attempts,
                "batch_size": self.claim_batch_size
            })
            db.commit()
            claimed = result.fetchall()
        except Exception as e:
            logger.error(f"Failed to claim jobs: {e}")
            db.rollback()
            return

        for row in claimed:
            job_id, url, source_execution_id = row
            self.process_job(db, job_id, url, new_token, source_execution_id)

    def _reserve_firecrawl_credit(self, _ignored_db: Session) -> bool:
        from app.core.config import settings
        if not getattr(settings, "firecrawl_enabled", True) or not settings.firecrawl_api_key or settings.firecrawl_monthly_budget <= 0:
            return False

        from app.services.ingestion import acquire_provider_request_slot
        from app.db.database import SessionLocal
        with SessionLocal() as local_db:
            return acquire_provider_request_slot(local_db, "firecrawl_monthly", cost_units=1)

    def _sync_firecrawl_budget(self, _ignored_db: Session):
        """
        Record operational payment_required exhaustion state for Firecrawl.
        Never modifies provider_usage, ensuring credit accounting remains strictly authoritative.
        """
        from app.services.firecrawl_quota import record_firecrawl_operation
        record_firecrawl_operation(operation="enrichment", cost_units=1, status="payment_required")

    def process_job(self, db: Session, job_id: int, url: str, token: str, source_execution_id: int | None = None):
        fallback_reason = None
        try:
            response = self.ssrf_client.fetch(url)
            response.raise_for_status()

            full_text = extract_job_description(response.text)
            self.complete_success(db, job_id, token, full_text, source_execution_id)
            return

        except SSRFViolation as e:
            logger.info(f"SSRF violation for job {job_id}, evaluating snippet natively.")
            self.complete_with_snippet(db, job_id, token, str(e), source_execution_id, unsupported=True)
            return
        except httpx.RequestError as e:
            logger.info(f"Network RequestError for job {job_id}, retrying natively.")
            self.complete_failure(db, job_id, token, str(e))
            return
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (404, 410):
                self.complete_with_snippet(db, job_id, token, str(e), source_execution_id, unsupported=True)
                return
            elif status >= 500:
                self.complete_failure(db, job_id, token, str(e))
                return
            else:
                fallback_reason = f"HTTP {status}"
        except ExtractionError as e:
            fallback_reason = str(e)
        except Exception as e:
            logger.warning(f"Scraping failed for job {job_id} ({e})")
            print(f"Exception in process_job: {e}")
            self.complete_failure(db, job_id, token, str(e))
            return

        if fallback_reason:
            from app.core.config import settings
            from app.services.firecrawl_quota import record_firecrawl_operation

            max_enrichment_credits = getattr(settings, "firecrawl_enrichment_max_credits_per_run", 10)
            if self.credits_used_this_run + 1 > max_enrichment_credits:
                logger.info(
                    f"Enrichment per-run Firecrawl credit limit reached ({self.credits_used_this_run}/{max_enrichment_credits}). "
                    f"Skipping Firecrawl and falling back to snippet for job {job_id}."
                )
                self.complete_with_snippet(
                    db, job_id, token,
                    f"Fallback needed ({fallback_reason}) but per-run credit cap reached",
                    source_execution_id,
                    unsupported=True
                )
                return

            if not self._reserve_firecrawl_credit(db):
                logger.info(f"Firecrawl budget exhausted or disabled. Snippet fallback for job {job_id}.")
                self.complete_with_snippet(db, job_id, token, f"Fallback needed ({fallback_reason}) but budget exhausted", source_execution_id, unsupported=True)
                return

            self.credits_used_this_run += 1

            try:
                from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError

                fc_client = FirecrawlClient(settings.firecrawl_api_key)
                markdown = fc_client.scrape(url)

                lower_md = markdown.lower()
                anti_bot_markers = ["please enable cookies", "checking your browser", "enable javascript", "just a moment...", "access denied"]
                if any(m in lower_md for m in anti_bot_markers):
                    raise FirecrawlError("Firecrawl markdown contains anti-bot markers", status_code=200)

                redirect_markers = [
                    "you are now being redirected",
                    "you are being redirected",
                    "if you are not redirected",
                    "view ad here",
                    "redirecting you to",
                    "please wait while we redirect you",
                    "please wait while you are redirected",
                ]
                if any(m in lower_md for m in redirect_markers):
                    raise FirecrawlError("Firecrawl markdown contains redirect/interstitial markers", status_code=200)

                fallback_markers = [
                    "interactive scripts did not run",
                    "interactive scripts could not run",
                    "interactive scripts failed to run",
                    "interactive scripts failed to execute",
                    "this page displays a fallback",
                    "page displays a fallback",
                    "displays a fallback because",
                    "fallback because interactive scripts",
                    "failure to load scripts or stylesheets",
                    "disabled javascript or failure to load",
                ]
                if any(m in lower_md for m in fallback_markers):
                    raise FirecrawlError("Firecrawl markdown contains script fallback/interstitial markers", status_code=200)

                if len(markdown) < 200:
                    raise FirecrawlError("Firecrawl markdown too short, proxy likely blocked", status_code=200)

                record_firecrawl_operation(operation="enrichment", cost_units=1, status="success")
                self.complete_success(db, job_id, token, markdown, source_execution_id)

            except FirecrawlError as e:
                if e.status_code == 402:
                    logger.error("Firecrawl returned 402 Payment Required. Recording exhaustion state.")
                    self._sync_firecrawl_budget(db)
                    self.complete_with_snippet(db, job_id, token, f"Firecrawl exhausted: {e}", source_execution_id, unsupported=True)
                elif e.status_code is None or e.status_code in (408, 429) or e.status_code >= 500:
                    record_firecrawl_operation(operation="enrichment", cost_units=1, status="failed")
                    self.complete_failure(db, job_id, token, f"Firecrawl transient error: {e}")
                else:
                    record_firecrawl_operation(operation="enrichment", cost_units=1, status="failed")
                    self.complete_with_snippet(db, job_id, token, f"Firecrawl content failure: {e}", source_execution_id, unsupported=True)
            except Exception as e:
                record_firecrawl_operation(operation="enrichment", cost_units=1, status="failed")
                self.complete_with_snippet(db, job_id, token, f"Firecrawl unhandled error: {e}", source_execution_id, unsupported=True)

    def complete_success(self, db: Session, job_id: int, token: str, new_desc: str, source_execution_id: int | None = None):
        try:
            with db.begin_nested():
                job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if not job or not job.description_is_snippet:
                    return

                # Layer 3: Post-enrichment content validation
                is_valid, validation_reason = validate_posting_content(job.title, new_desc)
                if not is_valid:
                    logger.info("Enriched content rejected as listing job_id=%s reason=%s", job_id, validation_reason)
                    update_enrichment_q = text("""
                        UPDATE job_enrichments SET
                            status = 'unsupported',
                            error_reason = :error_reason,
                            result_telemetry = CAST(:telemetry AS jsonb),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE job_id = :job_id
                          AND lease_token = :token
                          AND lease_expires_at > clock_timestamp()
                        RETURNING job_id;
                    """)
                    db.execute(update_enrichment_q, {
                        "job_id": job_id,
                        "token": token,
                        "error_reason": f"rejected_listing_content: {validation_reason}"[:255],
                        "telemetry": json.dumps({"validation": "rejected_listing", "reason": validation_reason}),
                    })
                    db.execute(text("""
                        DELETE FROM recommendation_history
                        WHERE job_id = :job_id AND delivery_id IS NULL
                    """), {"job_id": job.id})
                    db.commit()
                    return

                update_enrichment_q = text("""
                    UPDATE job_enrichments SET
                        status = 'success',
                        error_reason = NULL,
                        result_telemetry = CAST(:telemetry AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = :job_id
                      AND lease_token = :token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING job_id;
                """)
                res = db.execute(update_enrichment_q, {
                    "job_id": job_id,
                    "token": token,
                    "telemetry": json.dumps({"validation": "validated_posting", "text_length": len(new_desc)}),
                })
                if not res.fetchone():
                    return

                job.description = new_desc
                job.description_is_snippet = False
                job.updated_at = db.execute(text("SELECT CURRENT_TIMESTAMP")).scalar()

                is_eligible = is_fresher_eligible(
                    title=job.title,
                    description=job.description,
                    is_snippet=False
                )

                if is_eligible:
                    new_recs = 0
                    job_resp = JobResponse.model_validate(job)

                    # Score for all active users
                    active_user_ids = db.query(UserSearch.user_id).filter(UserSearch.enabled == True).distinct().all()
                    for (uid,) in active_user_ids:
                        u_profile = db.query(UserProfile).filter(UserProfile.user_id == uid).first()
                        prefs = RecommendationPreferences(
                            preferred_roles=u_profile.preferred_roles if u_profile else None,
                            skills=u_profile.skills if u_profile else None,
                            preferred_locations=u_profile.preferred_locations if u_profile else None,
                            remote_preference=u_profile.remote_preference if u_profile else None,
                            experience_years=u_profile.experience_years if u_profile else None
                        )

                        match_res = calculate_match(job_resp, prefs)
                        if match_res.score >= 50:
                            reasons_data = [
                                r.model_dump() if hasattr(r, 'model_dump') else r
                                for r in match_res.reasons
                            ] if match_res.reasons else None
                            stmt = insert(RecommendationHistoryModel).values(
                                user_id=uid,
                                job_id=job.id,
                                score=match_res.score,
                                reasons=reasons_data
                            ).on_conflict_do_nothing(
                                index_elements=['user_id', 'job_id']
                            ).returning(RecommendationHistoryModel.id)
                            res = db.execute(stmt)
                            if res.scalar() is not None:
                                new_recs += 1
                    if source_execution_id:
                        db.execute(text("""
                            UPDATE search_execution
                            SET jobs_fresher_eligible = COALESCE(jobs_fresher_eligible, 0) + 1,
                                recommendations_created = COALESCE(recommendations_created, 0) + :recs
                            WHERE id = :eid AND status NOT IN ('failed', 'abandoned')
                        """), {"recs": new_recs, "eid": source_execution_id})
                else:
                    db.execute(text("""
                        DELETE FROM recommendation_history
                        WHERE job_id = :job_id AND delivery_id IS NULL
                    """), {"job_id": job.id})

            db.commit()
        except Exception as e:
            logger.error(f"Error completing success for job {job_id}: {e}")
            print(f"Error completing success for job {job_id}: {e}")
            db.rollback()

    def complete_with_snippet(self, db: Session, job_id: int, token: str, reason: str, source_execution_id: int | None = None, unsupported: bool = False):
        try:
            with db.begin_nested():
                job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if not job:
                    return

                # Layer 3: Snippet content validation
                is_valid, validation_reason = validate_posting_content(job.title, job.description or "", is_snippet=True)
                if not is_valid:
                    logger.info("Snippet content rejected as listing job_id=%s reason=%s", job_id, validation_reason)
                    update_enrichment_q = text("""
                        UPDATE job_enrichments SET
                            status = 'unsupported',
                            error_reason = :reason,
                            result_telemetry = CAST(:telemetry AS jsonb),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE job_id = :job_id
                          AND lease_token = :token
                          AND lease_expires_at > clock_timestamp()
                        RETURNING job_id;
                    """)
                    db.execute(update_enrichment_q, {
                        "job_id": job_id,
                        "token": token,
                        "reason": f"rejected_listing_content: {validation_reason}"[:255],
                        "telemetry": json.dumps({"validation": "rejected_listing", "reason": validation_reason}),
                    })
                    db.execute(text("""
                        DELETE FROM recommendation_history
                        WHERE job_id = :job_id AND delivery_id IS NULL
                    """), {"job_id": job.id})
                    db.commit()
                    return

                # Do not retry if we are evaluating the snippet as a fallback. Mark it unsupported to end the lifecycle.
                update_enrichment_q = text("""
                    UPDATE job_enrichments SET
                        status = 'unsupported',
                        error_reason = :reason,
                        result_telemetry = CAST(:telemetry AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = :job_id
                      AND lease_token = :token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING job_id;
                """)
                res = db.execute(update_enrichment_q, {
                    "job_id": job_id,
                    "token": token,
                    "reason": reason[:255],
                    "telemetry": json.dumps({"validation": "snippet_fallback", "reason": reason[:100]}),
                })
                if not res.fetchone():
                    return

                is_eligible = is_fresher_eligible(
                    title=job.title,
                    description=job.description,
                    is_snippet=job.description_is_snippet
                )

                if is_eligible:
                    new_recs = 0
                    job_resp = JobResponse.model_validate(job)

                    active_user_ids = db.query(UserSearch.user_id).filter(UserSearch.enabled == True).distinct().all()
                    for (uid,) in active_user_ids:
                        u_profile = db.query(UserProfile).filter(UserProfile.user_id == uid).first()
                        prefs = RecommendationPreferences(
                            preferred_roles=u_profile.preferred_roles if u_profile else None,
                            skills=u_profile.skills if u_profile else None,
                            preferred_locations=u_profile.preferred_locations if u_profile else None,
                            remote_preference=u_profile.remote_preference if u_profile else None,
                            experience_years=u_profile.experience_years if u_profile else None
                        )

                        match_res = calculate_match(job_resp, prefs)
                        if match_res.score >= 50:
                            reasons_data = [
                                r.model_dump() if hasattr(r, 'model_dump') else r
                                for r in match_res.reasons
                            ] if match_res.reasons else None
                            stmt = insert(RecommendationHistoryModel).values(
                                user_id=uid,
                                job_id=job.id,
                                score=match_res.score,
                                reasons=reasons_data
                            ).on_conflict_do_nothing(
                                index_elements=['user_id', 'job_id']
                            ).returning(RecommendationHistoryModel.id)
                            res = db.execute(stmt)
                            if res.scalar() is not None:
                                new_recs += 1
                    if source_execution_id:
                        db.execute(text("""
                            UPDATE search_execution
                            SET jobs_fresher_eligible = COALESCE(jobs_fresher_eligible, 0) + 1,
                                recommendations_created = COALESCE(recommendations_created, 0) + :recs
                            WHERE id = :eid AND status NOT IN ('failed', 'abandoned')
                        """), {"recs": new_recs, "eid": source_execution_id})
                else:
                    db.execute(text("""
                        DELETE FROM recommendation_history
                        WHERE job_id = :job_id AND delivery_id IS NULL
                    """), {"job_id": job.id})

            db.commit()
        except Exception as e:
            logger.error(f"Error completing fallback snippet for job {job_id}: {e}")
            db.rollback()
            self.complete_failure(db, job_id, token, f"Fallback failed: {str(e)[:100]}", unsupported=False)

    def complete_failure(self, db: Session, job_id: int, token: str, reason: str, unsupported: bool = False):
        try:
            with db.begin_nested():
                status = 'unsupported' if unsupported else 'retry'
                update_q = text("""
                    UPDATE job_enrichments SET
                        status = CASE WHEN attempts + 1 >= :max_attempts AND :status = 'retry' THEN 'failure' ELSE :status END,
                        attempts = attempts + 1,
                        error_reason = :reason,
                        result_telemetry = CAST(:telemetry AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = :job_id
                      AND lease_token = :token
                      AND lease_expires_at > clock_timestamp()
                """)
                db.execute(update_q, {
                    "max_attempts": self.max_attempts,
                    "status": status,
                    "reason": reason[:255],
                    "telemetry": json.dumps({"failure": reason[:100]}),
                    "job_id": job_id,
                    "token": token
                })
            db.commit()
        except Exception as e:
            logger.error(f"Error completing failure for job {job_id}: {e}")
            db.rollback()
