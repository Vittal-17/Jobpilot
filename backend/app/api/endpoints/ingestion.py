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
        result = run_ingestion(db, "adzuna", provider, query)

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
        result = run_ingestion(db, "jooble", provider, query)
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

            from app.services.search_selector import generate_candidates
            candidate = next(
                (c for c in generate_candidates() if c.candidate_id == claim.candidate_id),
                None,
            )
            if candidate is None:
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

        result = run_ingestion(db, selected_provider.value, provider, query, intent.execution_id)
        if result.failed > 0:

            raise HTTPException(status_code=502, detail="Provider execution failed")
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
            except (ProviderNotConfigured, ProviderUnavailable, ProviderQuotaExhausted):
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
                keywords=result.candidate.role_canonical,
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
    except ProviderQuotaExhausted:
        raise HTTPException(status_code=429, detail="All providers are quota exhausted")
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
