import logging
logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.providers.types import ProviderName
from app.providers.registry import create_provider
from app.services.ingestion import (
    run_ingestion,
    DatabaseUnavailable,
    InvalidExecutionTransition,
    RateLimitExceeded,
)
from app.services.provider_router import (
    ProviderNotConfigured,
    ProviderQuotaExhausted,
    ProviderRoutingUnavailable,
    ProviderUnavailable,
)
from app.core.config import settings
import secrets

router = APIRouter()

def verify_api_key(x_api_key: str | None = Header(default=None, description="Internal Orchestration API Key")):
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_secret_key):
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.post("/adzuna", response_model=IngestionResult, dependencies=[Depends(verify_api_key)])
def ingest_adzuna(query: JobSearchQuery, db: Session = Depends(get_db)):
    try:
        provider = create_provider(ProviderName.ADZUNA)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ingestion endpoint unhandled error"); raise HTTPException(status_code=500, detail="Internal server error")

    from app.providers.exceptions import ProviderConfigurationError
    try:
        result, job_ids = run_ingestion(db, "adzuna", provider, query)

        if result.failed > 0:
            raise HTTPException(status_code=502, detail="Adzuna provider failed")
        return result
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="Provider request quota exceeded")
    except DatabaseUnavailable:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except ProviderConfigurationError:
        raise HTTPException(status_code=500, detail="Provider configuration error")
    except Exception:
        # P0: No internal exception leakage
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/jooble", response_model=IngestionResult, dependencies=[Depends(verify_api_key)])
def ingest_jooble(query: JobSearchQuery, db: Session = Depends(get_db)):
    try:
        provider = create_provider(ProviderName.JOOBLE)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ingestion endpoint unhandled error"); raise HTTPException(status_code=500, detail="Internal server error")

    from app.providers.exceptions import ProviderConfigurationError
    try:
        result, job_ids = run_ingestion(db, "jooble", provider, query)
        if result.failed > 0:
            raise HTTPException(status_code=502, detail="Jooble provider failed")
        return result
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="Provider request quota exceeded")
    except DatabaseUnavailable:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except ProviderConfigurationError:
        raise HTTPException(status_code=500, detail="Provider configuration error")
    except Exception:
        # P0: No internal exception leakage
        raise HTTPException(status_code=500, detail="Internal server error")


from app.schemas.job_search import CanonicalSearchIntent

@router.post("/internal/search", response_model=IngestionResult, dependencies=[Depends(verify_api_key)])
def internal_execute_search(intent: CanonicalSearchIntent, db: Session = Depends(get_db)):
    from app.providers.exceptions import ProviderConfigurationError
    try:
        selected_provider = intent.provider
        if intent.execution_id is not None:
            from app.db.models.search_execution import SearchExecutionModel
            claim = db.query(SearchExecutionModel).filter(SearchExecutionModel.id == intent.execution_id).first()
            if not claim:
                raise HTTPException(status_code=404, detail="Execution not found")
            if claim.status != 'selected':
                raise HTTPException(status_code=409, detail="Execution is not selectable")
            if not claim.provider_name:
                raise HTTPException(status_code=409, detail="Execution has no provider decision")
            if selected_provider is None or selected_provider.value != claim.provider_name:
                raise HTTPException(status_code=409, detail="Execution provider does not match routing decision")

            from app.services.search_selector import resolve_candidate
            candidate = resolve_candidate(db, claim.candidate_id)
            if candidate is None:
                _best_effort_close_routing_claim(db, intent.execution_id, "stale candidate")
                raise HTTPException(status_code=409, detail="Execution candidate is no longer valid")

            expected = (
                candidate.role_id,
                candidate.role_canonical,
                candidate.location_id,
                candidate.location_canonical,
                candidate.priority,
            )
            supplied = (
                intent.role_id,
                intent.keywords,
                intent.location_id,
                intent.location,
                intent.priority if intent.priority is not None else candidate.priority,
            )
            if supplied != expected:
                raise HTTPException(status_code=409, detail="Execution does not match selected candidate")

        elif selected_provider is None:
            selected_provider = ProviderName.ADZUNA

        provider = create_provider(selected_provider)
        query = JobSearchQuery(
            keywords=intent.keywords,
            location=intent.location,
            radius_km=None,
            page=1,
            page_size=20,
        )

        result, job_ids = run_ingestion(db, selected_provider.value, provider, query, intent.execution_id)
        if result.failed > 0:
            raise HTTPException(status_code=502, detail="Provider execution failed")

        if intent.execution_id is not None and job_ids:
            try:
                from app.db.models.user_search import UserSearch
                from app.db.models.user_profile import UserProfile
                from app.schemas.match import RecommendationPreferences
                from app.services.matching_service import calculate_match
                from app.schemas.job import JobResponse
                from app.db.models.job import JobModel
                from sqlalchemy.dialects.postgresql import insert
                from app.db.models.recommendation_history import RecommendationHistoryModel

                jobs = db.query(JobModel).filter(JobModel.id.in_(job_ids)).all()
                active_user_ids = db.query(UserSearch.user_id).filter(UserSearch.enabled == True).distinct().all()
                active_user_ids = [r[0] for r in active_user_ids]

                if active_user_ids and jobs:
                    for uid in active_user_ids:
                        u_profile = db.query(UserProfile).filter(UserProfile.user_id == uid).first()
                        prefs = RecommendationPreferences(
                            preferred_roles=u_profile.preferred_roles if u_profile else None,
                            skills=u_profile.skills if u_profile else None,
                            preferred_locations=u_profile.preferred_locations if u_profile else None,
                            remote_preference=u_profile.remote_preference if u_profile else None,
                            experience_years=u_profile.experience_years if u_profile else None
                        )

                        # Filter out jobs already recommended to this user
                        existing_recs = db.query(RecommendationHistoryModel.job_id).filter(
                            RecommendationHistoryModel.user_id == uid,
                            RecommendationHistoryModel.job_id.in_(job_ids)
                        ).all()
                        existing_job_ids = {r[0] for r in existing_recs}

                        scored_jobs = []
                        for job in jobs:
                            if job.id in existing_job_ids:
                                continue
                            job_resp = JobResponse.model_validate(job)
                            from app.services.eligibility import is_fresher_eligible
                            if not is_fresher_eligible(job_resp.title, job_resp.description):
                                continue

                            match_res = calculate_match(job_resp, prefs)
                            if match_res.score >= 50:
                                scored_jobs.append((match_res.score, job.id))

                        # Actually select the best jobs (top 5), not merely a match above a score threshold
                        scored_jobs.sort(key=lambda x: (-x[0], x[1]))
                        top_jobs = scored_jobs[:5]

                        for score, job_id in top_jobs:
                            stmt = insert(RecommendationHistoryModel).values(
                                user_id=uid,
                                job_id=job_id
                            ).on_conflict_do_nothing(
                                index_elements=['user_id', 'job_id']
                            )
                            db.execute(stmt)
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.exception("Failed to process recommendations")
                raise HTTPException(status_code=500, detail="Failed to process recommendations")

        return result
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="Provider request quota exceeded")
    except DatabaseUnavailable:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except InvalidExecutionTransition:
        raise HTTPException(status_code=409, detail="Execution state conflict")
    except ProviderConfigurationError:
        raise HTTPException(status_code=500, detail="Provider configuration error")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ingestion endpoint unhandled error"); raise HTTPException(status_code=500, detail="Internal server error")

from pydantic import BaseModel, Field

class CycleContext(BaseModel):
    cycle_id: str | None = Field(None, max_length=64, min_length=1, pattern=r"^\S(.*\S)?$")

class SelectionResponse(BaseModel):
    action: str = 'execute'
    cycle_id: str | None = None
    execution_id: int | None = None
    intent: CanonicalSearchIntent | None = None
    candidate_id: str | None = None
    reason: str
    score: int | None = None
    policy_version: str
    provider: ProviderName | None = None
    provider_reason: str | None = None
    provider_policy_version: str | None = None


def _best_effort_close_routing_claim(
    db: Session, execution_id: int, error_message: str
) -> None:
    try:
        db.rollback()
        db.execute(
            text(
                "UPDATE search_execution SET status = 'failed', completed_at = CURRENT_TIMESTAMP, "
                "error_message = :error_message "
                "WHERE id = :execution_id AND status = 'selected'"
            ),
            {"execution_id": execution_id, "error_message": error_message},
        )
        db.commit()
    except Exception:
        db.rollback()
        import logging
        logging.getLogger(__name__).exception(
            "Unable to close provider routing claim execution_id=%s", execution_id
        )

@router.post("/internal/select-next", response_model=SelectionResponse, dependencies=[Depends(verify_api_key)])
def select_next_search_endpoint(context: CycleContext | None = None, db: Session = Depends(get_db)):
    from app.services.search_selector import select_next_search
    from app.services.provider_router import (
        ProviderNotConfigured,
        ProviderQuotaExhausted,
        ProviderRoutingUnavailable,
        ProviderUnavailable,
        route_provider,
    )
    try:
        cycle_id = context.cycle_id if context else None
        result = select_next_search(db, cycle_id=cycle_id)
        db.commit()
        if result.candidate:
            try:
                provider_decision = route_provider(db)
            except ProviderQuotaExhausted:
                _best_effort_close_routing_claim(
                    db, result.execution_id, "daily_provider_budget_exhausted"
                )
                return SelectionResponse(
                    action="stop",
                    reason="daily_provider_budget_exhausted",
                    policy_version=result.policy_version if hasattr(result, "policy_version") else "v1"
                )
            except (ProviderNotConfigured, ProviderUnavailable):
                _best_effort_close_routing_claim(
                    db, result.execution_id, "provider routing failed"
                )
                raise
            db.execute(
                text(
                    "UPDATE search_execution SET provider_name = :provider "
                    "WHERE id = :execution_id AND status = 'selected' AND provider_name IS NULL"
                ),
                {
                    "execution_id": result.execution_id,
                    "provider": provider_decision.provider.value,
                },
            )
            if db.execute(
                text(
                    "SELECT COUNT(*) FROM search_execution "
                    "WHERE id = :execution_id AND status = 'selected' "
                    "AND provider_name = :provider"
                ),
                {
                    "execution_id": result.execution_id,
                    "provider": provider_decision.provider.value,
                },
            ).scalar_one() != 1:
                db.rollback()
                raise InvalidExecutionTransition("Provider decision could not be bound")
            db.commit()
            import logging
            logging.getLogger(__name__).info(
                "Provider routed execution_id=%s candidate_id=%s provider=%s reason=%s policy_version=%s",
                result.execution_id,
                result.candidate.candidate_id,
                provider_decision.provider.value,
                provider_decision.reason,
                provider_decision.policy_version,
            )
            intent = CanonicalSearchIntent(
                role_id=result.candidate.role_id,
                keywords=result.candidate.query_variant or result.candidate.role_canonical,
                location_id=result.candidate.location_id,
                location=result.candidate.location_canonical,
                priority=result.candidate.priority,
                execution_id=result.execution_id,
                provider=provider_decision.provider,
            )
            return SelectionResponse(
                action="execute",
                cycle_id=cycle_id,
                intent=intent,
                execution_id=result.execution_id,
                candidate_id=result.candidate.candidate_id,
                reason=result.reason,
                score=result.score,
                policy_version=result.policy_version,
                provider=provider_decision.provider,
                provider_reason=provider_decision.reason,
                provider_policy_version=provider_decision.policy_version,
            )
        else:
            return SelectionResponse(
                action="stop",
                cycle_id=cycle_id,
                intent=None,
                execution_id=None,
                candidate_id=None,
                reason=result.reason,
                score=result.score,
                policy_version=result.policy_version
            )
    except (ProviderNotConfigured, ProviderUnavailable):
        raise HTTPException(status_code=503, detail="No provider is available")
    except ProviderRoutingUnavailable:
        if 'result' in locals() and result.execution_id is not None:
            _best_effort_close_routing_claim(
                db, result.execution_id, "provider routing unavailable"
            )
        raise HTTPException(status_code=503, detail="Provider routing temporarily unavailable")
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Selection engine failed")
        raise HTTPException(status_code=500, detail="Internal server error")

class NotificationClaimRequest(BaseModel):
    user_id: int
    delivery_id: str = Field(..., max_length=64, min_length=1)
    limit: int = Field(5, ge=1, le=50)

class NotificationRecommendation(BaseModel):
    job_id: int
    title: str
    company: str
    location: str | None
    url: str | None

class NotificationClaimResponse(BaseModel):
    user_id: int
    delivery_id: str
    recommendations: list[NotificationRecommendation]

class NotificationAcknowledgeRequest(BaseModel):
    user_id: int
    delivery_id: str = Field(..., max_length=64, min_length=1)

class NotificationAcknowledgeResponse(BaseModel):
    user_id: int
    delivery_id: str
    acknowledged: bool

@router.post("/internal/notifications/claim", response_model=NotificationClaimResponse, dependencies=[Depends(verify_api_key)])
def claim_notifications(req: NotificationClaimRequest, db: Session = Depends(get_db)):
    try:
        from sqlalchemy.exc import IntegrityError

        # 1. Try to record the delivery intent first
        try:
            with db.begin_nested():
                db.execute(
                    text("INSERT INTO notification_deliveries (delivery_id, user_id, claimed_at) VALUES (:delivery_id, :user_id, CURRENT_TIMESTAMP)"),
                    {"delivery_id": req.delivery_id, "user_id": req.user_id}
                )

                # 2. Race-safe claim using UPDATE ... WHERE id IN (...)
                claimed = db.execute(
                    text("""
                    WITH claim AS (
                        SELECT id FROM recommendation_history
                        WHERE user_id = :user_id AND delivery_id IS NULL
                        ORDER BY recommended_at ASC
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE recommendation_history r
                    SET delivery_id = :delivery_id
                    FROM claim
                    WHERE r.id = claim.id
                    RETURNING r.job_id
                    """),
                    {"user_id": req.user_id, "delivery_id": req.delivery_id, "limit": req.limit}
                ).scalars().all()
        except IntegrityError:
            # 3. Idempotency: The delivery_id already exists.
            # We must verify it belongs to THIS user to prevent silent cross-user collisions.
            db.rollback()
            owner = db.execute(
                text("SELECT user_id FROM notification_deliveries WHERE delivery_id = :delivery_id"),
                {"delivery_id": req.delivery_id}
            ).scalar()
            if owner is not None and owner != req.user_id:
                raise HTTPException(status_code=409, detail="delivery_id conflict")

        # Fetch jobs assigned to this delivery_id (whether newly claimed or previously claimed)
        jobs = db.execute(
            text("""
            SELECT j.id as job_id, j.title, j.company, j.location, j.url
            FROM recommendation_history r
            JOIN jobs j ON r.job_id = j.id
            WHERE r.user_id = :user_id AND r.delivery_id = :delivery_id
            ORDER BY r.recommended_at ASC
            """),
            {"user_id": req.user_id, "delivery_id": req.delivery_id}
        ).mappings().all()

        db.commit()
        return NotificationClaimResponse(
            user_id=req.user_id,
            delivery_id=req.delivery_id,
            recommendations=[NotificationRecommendation(**j) for j in jobs]
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        import logging
        logging.getLogger(__name__).exception("Failed to claim notifications")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/internal/notifications/acknowledge", response_model=NotificationAcknowledgeResponse, dependencies=[Depends(verify_api_key)])
def acknowledge_notifications(req: NotificationAcknowledgeRequest, db: Session = Depends(get_db)):
    try:
        result = db.execute(
            text("UPDATE notification_deliveries SET notified_at = CURRENT_TIMESTAMP WHERE user_id = :user_id AND delivery_id = :delivery_id AND notified_at IS NULL"),
            {"user_id": req.user_id, "delivery_id": req.delivery_id}
        )
        db.commit()
        return NotificationAcknowledgeResponse(user_id=req.user_id, delivery_id=req.delivery_id, acknowledged=result.rowcount > 0)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
