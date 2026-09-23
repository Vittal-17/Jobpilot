import uuid
import os
import socket
import logging
from datetime import datetime, timezone
from typing import List
import sqlalchemy
from sqlalchemy.orm import Session
from sqlalchemy import text

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

    def process_job(self, db: Session, job_id: int, url: str, token: str, source_execution_id: int | None = None):
        try:
            response = self.ssrf_client.fetch(url)
            response.raise_for_status()

            full_text = extract_job_description(response.text)

            self.complete_success(db, job_id, token, full_text, source_execution_id)

        except SSRFViolation as e:
            self.complete_failure(db, job_id, token, str(e), unsupported=True)
        except ExtractionError as e:
            self.complete_failure(db, job_id, token, str(e), unsupported=True)
        except Exception as e:
            print(f"Exception in process_job: {e}")
            self.complete_failure(db, job_id, token, str(e))

    def complete_success(self, db: Session, job_id: int, token: str, new_desc: str, source_execution_id: int | None = None):
        try:
            with db.begin_nested():
                update_enrichment_q = text("""
                    UPDATE job_enrichments SET
                        status = 'success',
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
                            res = db.execute(text("""
                                INSERT INTO recommendation_history (user_id, job_id, recommended_at)
                                VALUES (:user_id, :job_id, CURRENT_TIMESTAMP)
                                ON CONFLICT (user_id, job_id) DO NOTHING
                                RETURNING id
                            """), {"user_id": uid, "job_id": job.id})
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
