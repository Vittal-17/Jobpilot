from app.api.endpoints.ingestion import verify_api_key
from fastapi import Depends

from fastapi import FastAPI
from app.api.endpoints import ingestion

app = FastAPI(
    title="JobPilot API",
    version="0.1.0",
    description="Backend API for the JobPilot automation platform.",
)

app.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])

@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/health/ready")
def readiness_check() -> dict[str, str]:
    from sqlalchemy import text
    from app.db.database import engine
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database unavailable")

@app.get("/internal/health", dependencies=[Depends(verify_api_key)])
def internal_health_check() -> dict[str, str]:
    return {"status": "ok"}
