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
from app.db.models.user import User
import json
import re
from app.services.scraper.ssrf import SSRFClient, SSRFViolation
from app.services.scraper.extractor import extract_job_description, ExtractionError

logger = logging.getLogger(__name__)

from app.services.content_scanner import (
    MAX_CONTENT_CHARS, has_content_aggregate_count, title_aggregate_reason,
)

CONTENT_AGGREGATE_PATTERNS = [
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
    # 1. Title heuristics (universal across all job pages)
    aggregate_reason = title_aggregate_reason(title)
    if aggregate_reason:
        return False, aggregate_reason

    if not text or not isinstance(text, str):
        return False, "empty_content"
    if len(text) > MAX_CONTENT_CHARS:
        return False, "content_too_long"
    clean_text = text.strip()
    if not clean_text:
        return False, "empty_content"
    lower_text = clean_text.lower()

    # 2. Bot / Anti-bot / Login wall detection
    for pattern in BOT_OR_LOGIN_WALL_PATTERNS:
        if pattern.search(lower_text):
            return False, "bot_or_login_wall"

    # 3. Aggregator & pagination markers in content
    if has_content_aggregate_count(clean_text):
        return False, "aggregator_marker"
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
        from app.services.firecrawl_quota import reconcile_stale_enrichment_reservations
        reconcile_stale_enrichment_reservations(db)
        db.execute(text('''
            UPDATE job_enrichments SET
                status = 'failure',
                error_reason = 'Max attempts exceeded due to worker crashes',
                lease_holder = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE (status = 'result_pending' AND attempts >= :max_attempts)
               OR (status = 'in_progress'
               AND lease_expires_at <= clock_timestamp()
               AND attempts + 1 >= :max_attempts);
        '''), {"max_attempts": self.max_attempts})

        claim_query = text('''
            WITH claimed AS (
                SELECT job_id, status FROM job_enrichments
                WHERE status = 'pending'
                   OR (status = 'in_progress' AND lease_expires_at <= clock_timestamp() AND attempts + 1 < :max_attempts)
                   OR (status = 'retry' AND attempts < :max_attempts)
                    OR (status = 'result_pending' AND attempts < :max_attempts)
                 ORDER BY CASE WHEN status = 'result_pending' THEN 0 ELSE 1 END,
                          updated_at ASC, job_id ASC
                 FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            UPDATE job_enrichments e SET
                status = 'in_progress',
                lease_holder = :worker_id,
                 lease_token = md5(:new_token || ':' || e.job_id::text),
                lease_expires_at = clock_timestamp() + INTERVAL '5 minutes',
                attempts = CASE WHEN e.status = 'in_progress' OR e.staged_content IS NOT NULL THEN e.attempts + 1 ELSE e.attempts END
            FROM claimed c
            WHERE e.job_id = c.job_id
              RETURNING e.job_id, e.url, e.source_execution_id, e.lease_token, e.staged_content;
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
            job_id, url, source_execution_id, lease_token, staged_content = row
            self.process_job(db, job_id, url, lease_token, source_execution_id, staged_content)
            # ``process_job`` and terminal helpers deliberately do not commit
            # caller-owned work.  The batch runner is the explicit boundary.
            db.commit()

    def _reserve_firecrawl_credit(self, _ignored_db: Session, *, execution_id: int | None = None, job_id: int | None = None, lease_token: str | None = None) -> bool:
        return self._reserve_firecrawl_receipt(
            _ignored_db,
            execution_id=execution_id,
            job_id=job_id,
            lease_token=lease_token,
        ) is not None

    def _reserve_firecrawl_receipt(
        self,
        _ignored_db: Session,
        *,
        execution_id: int | None = None,
        job_id: int | None = None,
        lease_token: str | None = None,
        operation_key: str | None = None,
    ):
        """Return the durable paid-call receipt (bool facade above is legacy)."""
        from app.core.config import settings
        if not getattr(settings, "firecrawl_enabled", True) or not settings.firecrawl_api_key or settings.firecrawl_monthly_budget <= 0:
            return None

        from app.services.ingestion import reserve_provider_request
        from app.db.database import SessionLocal
        # A claimed enrichment has a stable replay key.  Direct/legacy quota
        # callers without ownership must receive a fresh key for every paid
        # attempt; using ``enrichment:None:None`` would make concurrent calls
        # incorrectly reuse one receipt and bypass the cap.
        request_key = operation_key
        if request_key is None and job_id is not None and lease_token is not None:
            request_key = f"enrichment:{job_id}:{lease_token}"
        with SessionLocal() as local_db:
            return reserve_provider_request(
                local_db, "firecrawl_monthly", cost_units=1,
                execution_id=execution_id, job_id=job_id, lease_token=lease_token,
                operation_key=request_key,
            )

    @staticmethod
    def is_lease_live(db: Session, job_id: int, token: str) -> bool:
        """Canonical live ownership probe for enrichment work."""
        row = db.execute(text(
            "SELECT 1 FROM job_enrichments WHERE job_id = :job_id "
            "AND status = 'in_progress' AND lease_token = :token "
            "AND lease_expires_at > clock_timestamp()"
        ), {"job_id": job_id, "token": token}).scalar()
        return bool(row)

    def fence_terminal_transition(
        self,
        db: Session,
        job_id: int,
        token: str,
        status: str,
        *,
        error_reason: str | None = None,
        telemetry: dict | None = None,
        increment_attempts: bool = False,
    ) -> bool:
        """Perform exactly one live-lease fenced terminal transition."""
        if status not in {"success", "failure", "unsupported"}:
            raise ValueError(f"Not a terminal enrichment status: {status}")
        result = db.execute(text(
            "UPDATE job_enrichments SET status = :status, error_reason = :reason, "
            "attempts = attempts + :increment, "
            "result_telemetry = CAST(:telemetry AS jsonb), "
            "lease_holder = NULL, lease_token = NULL, lease_expires_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE job_id = :job_id AND status = 'in_progress' "
            "AND lease_token = :token AND lease_expires_at > clock_timestamp() "
            "RETURNING job_id"
        ), {
            "job_id": job_id,
            "token": token,
            "status": status,
            "reason": error_reason,
            "telemetry": json.dumps(telemetry) if telemetry is not None else None,
            "increment": 1 if increment_attempts else 0,
        })
        return result.fetchone() is not None

    def _sync_firecrawl_budget(self, _ignored_db: Session):
        """Set the durable payment latch without changing usage accounting."""
        from app.services.firecrawl_quota import latch_firecrawl_payment_required
        latch_firecrawl_payment_required()

    @staticmethod
    def _settle_receipt(receipt, status: str, *, db: Session, **kwargs):
        """Keep the durable in-flight obligation if settlement is unavailable.

        Callers must not finalize a failed settlement. A reclaimed lease will
        reconcile its attributable unstaged receipt conservatively, while an
        exact staged receipt is recoverable without another provider call.
        """
        from app.services.firecrawl_quota import settle_provider_reservation
        try:
            return settle_provider_reservation(receipt, status, db=db, **kwargs)
        except Exception:
            logger.exception(
                "Unable to settle Firecrawl reservation id=%s status=%s",
                getattr(receipt, "reservation_id", receipt), status,
            )
            return None

    def process_job(self, db: Session, job_id: int, url: str, token: str, source_execution_id: int | None = None, staged_content: str | None = None):
        from app.services.firecrawl_quota import reconcile_stale_enrichment_reservations
        reconcile_stale_enrichment_reservations(db, job_id)
        # A stale worker must not even begin work.  In particular this probe
        # happens before a Firecrawl reservation or external call (H20).
        if not self.is_lease_live(db, job_id, token):
            return
        if staged_content:
            from app.services.firecrawl_quota import settle_provider_reservation
            rid = db.execute(text("SELECT staged_reservation_id FROM job_enrichments WHERE job_id=:jid"), {"jid": job_id}).scalar()
            try:
                settled = settle_provider_reservation(
                    rid, "succeeded", db=db, recovery_job_id=job_id, recovery_token=token,
                    metadata={"recovered_staged_result": True},
                ) if rid else None
                if settled is None or settled.status != "succeeded":
                    self.complete_failure(db, job_id, token, "blocked_paid_result: contradictory or missing reservation", terminal=True)
                    return
                self.complete_success(db, job_id, token, staged_content, source_execution_id)
            except Exception:
                logger.exception("Paid result recovery failed job_id=%s reservation_id=%s", job_id, rid)
                self.complete_failure(db, job_id, token, "paid_result_recovery_failed")
            return
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

            from app.services.scraper.firecrawl import FirecrawlClient
            if not FirecrawlClient.scrape_billing_contract_verified():
                self.complete_with_snippet(
                    db, job_id, token, "billing_contract_unverified", source_execution_id, unsupported=True
                )
                return

            receipt = self._reserve_firecrawl_receipt(
                db,
                execution_id=source_execution_id,
                job_id=job_id,
                lease_token=token,
                operation_key=f"enrichment:{job_id}:{token}",
            )
            if receipt is None:
                logger.info(f"Firecrawl budget exhausted or disabled. Snippet fallback for job {job_id}.")
                self.complete_with_snippet(db, job_id, token, f"Fallback needed ({fallback_reason}) but budget exhausted", source_execution_id, unsupported=True)
                return

            self.credits_used_this_run += 1

            try:
                from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError
                from app.services.firecrawl_quota import mark_provider_request_in_flight, cancel_reserved_provider_request

                # The lease may expire after reservation commit.  Do not make
                # an external call from a worker that no longer owns it.
                if not self.is_lease_live(db, job_id, token):
                    cancel_reserved_provider_request(receipt, db=db, reason="lease_expired")
                    return
                flight_receipt = mark_provider_request_in_flight(receipt, db=db)
                if flight_receipt is None:
                    cancel_reserved_provider_request(receipt, db=db, reason="lease_expired_or_reclaimed")
                    # Another invocation of this same lease may be making the
                    # call. Do not terminalize its job or its in-flight receipt.
                    return
                receipt = flight_receipt

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

                if not self._stage_paid_result(db, job_id, token, receipt, markdown):
                    self._settle_receipt(receipt, "unknown_after_crash", db=db, metadata={"reason": "lease_lost_before_result_stage"})
                    return
                settled = self._settle_receipt(receipt, "succeeded", db=db, metadata={"text_length": len(markdown)})
                if settled is None or settled.status != "succeeded":
                    return
                # A new lease will resume this staged content; do not pretend
                # that billing success alone made the result durable.
                return

            except FirecrawlError as e:
                if e.status_code == 402:
                    logger.error("Firecrawl returned 402 Payment Required. Recording exhaustion state.")
                    if self._settle_receipt(receipt, "payment_required", db=db, metadata={"status_code": 402, "external_call": True}) is None:
                        return
                    self._sync_firecrawl_budget(db)
                    self.complete_with_snippet(db, job_id, token, f"Firecrawl exhausted: {e}", source_execution_id, unsupported=True)
                elif e.status_code is None or e.status_code in (408, 429) or e.status_code >= 500:
                    if self._settle_receipt(receipt, "unknown_after_crash", db=db, metadata={"status_code": e.status_code, "external_call": True}) is None:
                        return
                    self.complete_failure(db, job_id, token, f"Firecrawl outcome unknown_after_crash: {e}", terminal=True)
                else:
                    if self._settle_receipt(receipt, "failed", db=db, metadata={"status_code": e.status_code, "external_call": True}) is None:
                        return
                    self.complete_with_snippet(db, job_id, token, f"Firecrawl content failure: {e}", source_execution_id, unsupported=True)
            except Exception as e:
                if self._settle_receipt(receipt, "unknown_after_crash", db=db, metadata={"error_type": type(e).__name__, "external_call": True}) is None:
                    return
                self.complete_failure(db, job_id, token, f"Firecrawl outcome unknown_after_crash: {e}", terminal=True)

    @staticmethod
    def _stage_paid_result(db, job_id, token, receipt, content):
        """Persist content + exact receipt independently of caller-owned work."""
        from app.services.firecrawl_quota import independent_bind
        with Session(independent_bind(db)) as stage_db:
            owned = stage_db.execute(text(
                "SELECT 1 FROM job_enrichments WHERE job_id=:jid AND status='in_progress' "
                "AND lease_token=:token AND lease_expires_at>clock_timestamp() FOR UPDATE"
            ), {"jid": job_id, "token": token}).scalar()
            if not owned:
                return False
            valid = stage_db.execute(text(
                "SELECT 1 FROM provider_reservations WHERE id=:rid AND job_id=:jid "
                "AND lease_token=:token AND status='in_flight' FOR UPDATE"
            ), {"rid": receipt.reservation_id, "jid": job_id, "token": token}).scalar()
            if not valid:
                return False
            stage_db.execute(text(
                "UPDATE job_enrichments SET status='result_pending', staged_content=:content, "
                "staged_reservation_id=:rid, result_telemetry=CAST(:telemetry AS jsonb), "
                "lease_holder=NULL, lease_token=NULL, lease_expires_at=NULL, updated_at=clock_timestamp() "
                "WHERE job_id=:jid"
            ), {"jid": job_id, "rid": receipt.reservation_id, "content": content,
                "telemetry": json.dumps({"text_length": len(content)})})
            stage_db.commit()
            return True

    def complete_success(self, db: Session, job_id: int, token: str, new_desc: str, source_execution_id: int | None = None):
        # Completion helpers intentionally leave commit ownership with the
        # caller (claim_and_process or an API/service transaction).
        self._complete_success(db, job_id, token, new_desc, source_execution_id)

    def _complete_success(self, db: Session, job_id: int, token: str, new_desc: str, source_execution_id: int | None = None):
        try:
            with db.begin_nested():
                job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if not job or not job.description_is_snippet:
                    return

                # Layer 3: Post-enrichment content validation
                is_valid, validation_reason = validate_posting_content(job.title, new_desc)
                if not is_valid:
                    logger.info("Enriched content rejected as listing job_id=%s reason=%s", job_id, validation_reason)
                    if not self.fence_terminal_transition(
                        db, job_id, token, "unsupported",
                        error_reason=f"rejected_listing_content: {validation_reason}"[:255],
                        telemetry={"validation": "rejected_listing", "reason": validation_reason},
                    ):
                        return
                    db.execute(text("""
                        DELETE FROM recommendation_history
                        WHERE job_id = :job_id AND delivery_id IS NULL
                    """), {"job_id": job.id})
                    return

                if not self.fence_terminal_transition(
                    db, job_id, token, "success",
                    telemetry={"validation": "validated_posting", "text_length": len(new_desc)},
                ):
                    return

                job.description = new_desc
                job.description_is_snippet = False
                job.updated_at = db.execute(text("SELECT CURRENT_TIMESTAMP")).scalar()

                from app.services.location_policy import posting_matches_retrieval_location
                retrieval_location = None
                if source_execution_id is not None:
                    retrieval_location = db.execute(text(
                        "SELECT retrieval_location FROM search_execution WHERE id=:eid"
                    ), {"eid": source_execution_id}).scalar()
                is_eligible = posting_matches_retrieval_location(
                    job.location, job.remote, retrieval_location
                ) and is_fresher_eligible(
                    title=job.title,
                    description=job.description,
                    is_snippet=False
                )

                if is_eligible:
                    new_recs = 0
                    job_resp = JobResponse.model_validate(job)

                    # Score for all active users
                    active_user_ids = db.query(UserSearch.user_id).join(
                        User, User.id == UserSearch.user_id
                    ).filter(
                        UserSearch.enabled.is_(True), User.is_active.is_(True)
                    ).distinct().all()
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
                                reasons=reasons_data,
                                source_execution_id=source_execution_id,
                            ).on_conflict_do_nothing(
                                index_elements=['user_id', 'job_id']
                            ).returning(RecommendationHistoryModel.id)
                            res = db.execute(stmt)
                            if res.scalar() is not None:
                                new_recs += 1
                    if source_execution_id:
                        from app.services.execution_outcomes import claim_execution_eligibility, increment_execution_quality
                        counted = claim_execution_eligibility(db, source_execution_id, job.id)
                        increment_execution_quality(db, source_execution_id, counted, new_recs)
                else:
                    # Location mismatch is scoped to this execution.  Do not
                    # remove recommendations created for another user/run.
                    pass

        except Exception as e:
            logger.error(f"Error completing success for job {job_id}: {e}")
            print(f"Error completing success for job {job_id}: {e}")
            db.rollback()

    def complete_with_snippet(self, db: Session, job_id: int, token: str, reason: str, source_execution_id: int | None = None, unsupported: bool = False):
        self._complete_with_snippet(db, job_id, token, reason, source_execution_id, unsupported)

    def _complete_with_snippet(self, db: Session, job_id: int, token: str, reason: str, source_execution_id: int | None = None, unsupported: bool = False):
        try:
            with db.begin_nested():
                job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if not job:
                    return

                # Layer 3: Snippet content validation
                is_valid, validation_reason = validate_posting_content(job.title, job.description or "", is_snippet=True)
                if not is_valid:
                    logger.info("Snippet content rejected as listing job_id=%s reason=%s", job_id, validation_reason)
                    if not self.fence_terminal_transition(
                        db, job_id, token, "unsupported",
                        error_reason=f"rejected_listing_content: {validation_reason}"[:255],
                        telemetry={"validation": "rejected_listing", "reason": validation_reason},
                    ):
                        return
                    db.execute(text("""
                        DELETE FROM recommendation_history
                        WHERE job_id = :job_id AND delivery_id IS NULL
                    """), {"job_id": job.id})
                    return

                # Do not retry if we are evaluating the snippet as a fallback. Mark it unsupported to end the lifecycle.
                if not self.fence_terminal_transition(
                    db, job_id, token, "unsupported",
                    error_reason=reason[:255],
                    telemetry={"validation": "snippet_fallback", "reason": reason[:100]},
                ):
                    return

                from app.services.location_policy import posting_matches_retrieval_location
                retrieval_location = None
                if source_execution_id is not None:
                    retrieval_location = db.execute(text(
                        "SELECT retrieval_location FROM search_execution WHERE id=:eid"
                    ), {"eid": source_execution_id}).scalar()
                is_eligible = posting_matches_retrieval_location(
                    job.location, job.remote, retrieval_location
                ) and is_fresher_eligible(
                    title=job.title,
                    description=job.description,
                    is_snippet=job.description_is_snippet
                )

                if is_eligible:
                    new_recs = 0
                    job_resp = JobResponse.model_validate(job)

                    active_user_ids = db.query(UserSearch.user_id).join(
                        User, User.id == UserSearch.user_id
                    ).filter(
                        UserSearch.enabled.is_(True), User.is_active.is_(True)
                    ).distinct().all()
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
                                reasons=reasons_data,
                                source_execution_id=source_execution_id,
                            ).on_conflict_do_nothing(
                                index_elements=['user_id', 'job_id']
                            ).returning(RecommendationHistoryModel.id)
                            res = db.execute(stmt)
                            if res.scalar() is not None:
                                new_recs += 1
                    if source_execution_id:
                        from app.services.execution_outcomes import claim_execution_eligibility, increment_execution_quality
                        counted = claim_execution_eligibility(db, source_execution_id, job.id)
                        increment_execution_quality(db, source_execution_id, counted, new_recs)
                else:
                    # Location mismatch is scoped to this execution.  Do not
                    # remove recommendations created for another user/run.
                    pass

        except Exception as e:
            logger.error(f"Error completing fallback snippet for job {job_id}: {e}")
            db.rollback()
            self.complete_failure(db, job_id, token, f"Fallback failed: {str(e)[:100]}", unsupported=False)

    def complete_failure(
        self,
        db: Session,
        job_id: int,
        token: str,
        reason: str,
        unsupported: bool = False,
        terminal: bool = False,
    ):
        try:
            with db.begin_nested():
                attempts = db.execute(text(
                    "SELECT attempts FROM job_enrichments WHERE job_id=:job_id "
                    "AND status='in_progress' AND lease_token=:token "
                    "AND lease_expires_at > clock_timestamp() FOR UPDATE"
                ), {"job_id": job_id, "token": token}).scalar()
                if attempts is None:
                    return
                if unsupported or terminal or attempts + 1 >= self.max_attempts:
                    self.fence_terminal_transition(
                        db, job_id, token, "unsupported" if unsupported else "failure",
                        error_reason=reason[:255], telemetry={"failure": reason[:100]},
                        increment_attempts=True,
                    )
                else:
                    # A native (unpaid) failure can retry.  Paid unknowns are
                    # always passed with terminal=True and cannot reach here.
                    db.execute(text(
                        "UPDATE job_enrichments SET status='retry', attempts=attempts+1, "
                        "error_reason=:reason, result_telemetry=CAST(:telemetry AS jsonb), "
                        "lease_holder=NULL, lease_token=NULL, lease_expires_at=NULL, "
                        "updated_at=CURRENT_TIMESTAMP WHERE job_id=:job_id "
                        "AND status='in_progress' AND lease_token=:token "
                        "AND lease_expires_at>clock_timestamp()"
                    ), {"job_id": job_id, "token": token, "reason": reason[:255],
                        "telemetry": json.dumps({"failure": reason[:100]})})
        except Exception as e:
            logger.error(f"Error completing failure for job {job_id}: {e}")
            db.rollback()
