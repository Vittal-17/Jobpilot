from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.providers.adzuna import AdzunaProvider
from app.providers.jooble import JoobleProvider
from app.services.ingestion import run_ingestion, RateLimitExceeded, DatabaseUnavailable
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
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

    from app.providers.exceptions import ProviderConfigurationError
    try:
        result = run_ingestion(db, "adzuna", provider, query)
        if result.failed > 0:
            raise HTTPException(status_code=502, detail="Adzuna provider failed")
        return result
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail="Provider request quota exceeded")
    except DatabaseUnavailable as e:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except ProviderConfigurationError as e:
        raise HTTPException(status_code=500, detail="Provider configuration error")
    except Exception as e:
        # P0: No internal exception leakage
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/jooble", response_model=IngestionResult, dependencies=[Depends(verify_api_key)])
def ingest_jooble(query: JobSearchQuery, db: Session = Depends(get_db)):
    try:
        provider = JoobleProvider()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

    from app.providers.exceptions import ProviderConfigurationError
    try:
        result = run_ingestion(db, "jooble", provider, query)
        if result.failed > 0:
            raise HTTPException(status_code=502, detail="Jooble provider failed")
        return result
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail="Provider request quota exceeded")
    except DatabaseUnavailable as e:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except ProviderConfigurationError as e:
        raise HTTPException(status_code=500, detail="Provider configuration error")
    except Exception as e:
        # P0: No internal exception leakage
        raise HTTPException(status_code=500, detail="Internal server error")


from app.schemas.job_search import CanonicalSearchIntent

@router.post("/internal/search", response_model=IngestionResult, dependencies=[Depends(verify_api_key)])
def internal_execute_search(intent: CanonicalSearchIntent, db: Session = Depends(get_db)):
    # 005.6 - Force Adzuna selection for this milestone.
    try:
        provider = AdzunaProvider()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

    # Map the orchestration intent to the business logic schema
    query = JobSearchQuery(
        keywords=intent.keywords,
        location=intent.location,
        radius_km=None,
        page=1,
        page_size=20
    )

    from app.providers.exceptions import ProviderConfigurationError
    try:
        # Existing quota and persistence logic
        result = run_ingestion(db, "adzuna", provider, query)
        if result.failed > 0:
            raise HTTPException(status_code=502, detail="Adzuna provider failed")
        return result
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail="Provider request quota exceeded")
    except DatabaseUnavailable as e:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except ProviderConfigurationError as e:
        raise HTTPException(status_code=500, detail="Provider configuration error")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
