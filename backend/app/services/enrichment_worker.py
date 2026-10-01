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
from app.services.scraper.ssrf import SSRFClient, SSRFViolation
from app.services.scraper.extractor import extract_job_description, ExtractionError

logger = logging.getLogger(__name__)

class EnrichmentWorker:
    def __init__(self, max_attempts=3, claim_batch_size=10, worker_id: str | None = None):
        self.max_attempts = max_attempts
        self.claim_batch_size = claim_batch_size
        self.worker_id = worker_id or os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
        self.ssrf_client = SSRFClient()

    def run(self):
        with SessionLocal() as db:
            self.claim_and_process(db)

    def claim_and_process(self, db: Session):
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
        if not settings.firecrawl_api_key or settings.firecrawl_monthly_budget <= 0:
            return False

        from app.db.database import SessionLocal
        with SessionLocal() as local_db:
            try:
                stmt = text("""
                    INSERT INTO provider_usage (provider_name, usage_date, request_count)
                    VALUES ('firecrawl_monthly', DATE_TRUNC('month', CURRENT_DATE)::DATE, 1)
                    ON CONFLICT (provider_name, usage_date)
                    DO UPDATE SET request_count = provider_usage.request_count + 1
                    WHERE provider_usage.request_count < :monthly_limit
                    RETURNING request_count;
                """)
                res = local_db.execute(stmt, {"monthly_limit": settings.firecrawl_monthly_budget}).scalar()
                local_db.commit()
                return res is not None
            except Exception as e:
                logger.error(f"Failed to reserve Firecrawl credit: {e}")
                local_db.rollback()
                return False

    def _sync_firecrawl_budget(self, _ignored_db: Session):
        from app.core.config import settings
        from app.db.database import SessionLocal
        with SessionLocal() as local_db:
            try:
                stmt = text("""
                    UPDATE provider_usage
                    SET request_count = GREATEST(request_count, :monthly_limit)
                    WHERE provider_name = 'firecrawl_monthly' AND usage_date = DATE_TRUNC('month', CURRENT_DATE)::DATE;
                """)
                local_db.execute(stmt, {"monthly_limit": settings.firecrawl_monthly_budget})
                local_db.commit()
            except Exception as e:
                logger.error(f"Failed to sync Firecrawl budget: {e}")
                local_db.rollback()

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
            if not self._reserve_firecrawl_credit(db):
                logger.info(f"Firecrawl budget exhausted or disabled. Snippet fallback for job {job_id}.")
                self.complete_with_snippet(db, job_id, token, f"Fallback needed ({fallback_reason}) but budget exhausted", source_execution_id, unsupported=True)
                return

            try:
                from app.core.config import settings
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

                if len(markdown) < 200:
                    raise FirecrawlError("Firecrawl markdown too short, proxy likely blocked", status_code=200)

                self.complete_success(db, job_id, token, markdown, source_execution_id)

            except FirecrawlError as e:
                if e.status_code == 402:
                    logger.error("Firecrawl returned 402 Payment Required. Syncing budget to ceiling.")
                    self._sync_firecrawl_budget(db)
                    self.complete_with_snippet(db, job_id, token, f"Firecrawl exhausted: {e}", source_execution_id, unsupported=True)
                elif e.status_code is None or e.status_code in (408, 429) or e.status_code >= 500:
                    self.complete_failure(db, job_id, token, f"Firecrawl transient error: {e}")
                else:
                    self.complete_with_snippet(db, job_id, token, f"Firecrawl content failure: {e}", source_execution_id, unsupported=True)
            except Exception as e:
                self.complete_with_snippet(db, job_id, token, f"Firecrawl unhandled error: {e}", source_execution_id, unsupported=True)

    def complete_success(self, db: Session, job_id: int, token: str, new_desc: str, source_execution_id: int | None = None):
        try:
            with db.begin_nested():
                update_enrichment_q = text("""
                    UPDATE job_enrichments SET
                        status = 'success',
                        error_reason = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = :job_id
                      AND lease_token = :token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING job_id;
                """)
                res = db.execute(update_enrichment_q, {"job_id": job_id, "token": token})
                if not res.fetchone():
                    return

                job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if not job or not job.description_is_snippet:
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
                # Do not retry if we are evaluating the snippet as a fallback. Mark it unsupported to end the lifecycle.
                update_enrichment_q = text(f"""
                    UPDATE job_enrichments SET
                        status = 'unsupported',
                        error_reason = :reason,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = :job_id
                      AND lease_token = :token
                      AND lease_expires_at > clock_timestamp()
                    RETURNING job_id;
                """)
                res = db.execute(update_enrichment_q, {"job_id": job_id, "token": token, "reason": reason[:255]})
                if not res.fetchone():
                    return

                job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if not job:
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
                update_q = text(f"""
                    UPDATE job_enrichments SET
                        status = CASE WHEN attempts + 1 >= :max_attempts AND :status = 'retry' THEN 'failure' ELSE :status END,
                        attempts = attempts + 1,
                        error_reason = :reason,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = :job_id
                      AND lease_token = :token
                      AND lease_expires_at > clock_timestamp()
                """)
                db.execute(update_q, {
                    "max_attempts": self.max_attempts,
                    "status": status,
                    "reason": reason[:255],
                    "job_id": job_id,
                    "token": token
                })
            db.commit()
        except Exception as e:
            logger.error(f"Error completing failure for job {job_id}: {e}")
            db.rollback()
