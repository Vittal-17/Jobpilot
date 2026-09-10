from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.providers.adzuna import AdzunaProvider
from app.providers.jooble import JoobleProvider
from app.services.ingestion import (
    run_ingestion,
    DatabaseUnavailable,
    InvalidExecutionTransition,
    RateLimitExceeded,
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
        provider = AdzunaProvider()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

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
        provider = JoobleProvider()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

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
        if intent.execution_id is not None:
            from app.db.models.search_execution import SearchExecutionModel
            claim = db.query(SearchExecutionModel).filter(SearchExecutionModel.id == intent.execution_id).first()
            if not claim:
                raise HTTPException(status_code=404, detail="Execution not found")
            if claim.status != 'selected':
                raise HTTPException(status_code=409, detail="Execution is not selectable")

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

        provider = AdzunaProvider()
        query = JobSearchQuery(
            keywords=intent.keywords,
            location=intent.location,
            radius_km=None,
            page=1,
            page_size=20,
        )

        result = run_ingestion(db, "adzuna", provider, query, intent.execution_id)
        if result.failed > 0:

            raise HTTPException(status_code=502, detail="Adzuna provider failed")
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
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

from pydantic import BaseModel

class SelectionResponse(BaseModel):
    execution_id: int | None = None
    intent: CanonicalSearchIntent | None = None
    candidate_id: str | None = None
    reason: str
    score: int | None = None
    policy_version: str

@router.post("/internal/select-next", response_model=SelectionResponse, dependencies=[Depends(verify_api_key)])
def select_next_search_endpoint(db: Session = Depends(get_db)):
    from app.services.search_selector import select_next_search
    try:
        result = select_next_search(db)
        if result.candidate:
            intent = CanonicalSearchIntent(
                role_id=result.candidate.role_id,
                keywords=result.candidate.role_canonical,
                location_id=result.candidate.location_id,
                location=result.candidate.location_canonical,
                priority=result.candidate.priority,
                execution_id=result.execution_id
            )
            return SelectionResponse(
                intent=intent,
                execution_id=result.execution_id,
                candidate_id=result.candidate.candidate_id,
                reason=result.reason,
                score=result.score,
                policy_version=result.policy_version
            )
        else:
            return SelectionResponse(
                intent=None,
                execution_id=None,
                candidate_id=None,
                reason=result.reason,
                score=result.score,
                policy_version=result.policy_version
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Selection engine failed")
        raise HTTPException(status_code=500, detail="Internal server error")
