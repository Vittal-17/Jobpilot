from app.api.endpoints.ingestion import verify_api_key
from fastapi import Depends

from fastapi import FastAPI, Request
from app.api.endpoints import ingestion, auth, users, profile, jobs, saved_jobs, applications, searches

app = FastAPI(
    title="JobPilot API",
    version="0.1.0",
    description="Backend API for the JobPilot automation platform.",
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; img-src 'self' data: fastapi.tiangolo.com"
    return response

app.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/v1", tags=["users"])
app.include_router(profile.router, prefix="/v1", tags=["profile"])
app.include_router(jobs.router, prefix="/v1/jobs", tags=["jobs"])
app.include_router(saved_jobs.router, prefix="/v1/saved", tags=["saved_jobs"])
app.include_router(applications.router, prefix="/v1/applications", tags=["applications"])
app.include_router(searches.router, prefix="/v1/searches", tags=["searches"])


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
