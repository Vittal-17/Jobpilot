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
