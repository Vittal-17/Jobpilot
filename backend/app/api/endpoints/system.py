from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pydantic import BaseModel
from datetime import datetime

from app.db.database import get_db
from app.db.models.job import JobModel
from app.db.models.search_execution import SearchExecutionModel

router = APIRouter()

class SystemStatusResponse(BaseModel):
    engine_active: bool
    last_sync: datetime | None
    latest_execution_status: str | None
    total_processed: int

@router.get("/status", response_model=SystemStatusResponse)
def get_system_status(
    db: Session = Depends(get_db)
):
    # Authoritative execution health
    last_sync = db.scalar(
        select(SearchExecutionModel.completed_at)
        .where(SearchExecutionModel.status == 'succeeded')
        .order_by(SearchExecutionModel.completed_at.desc())
        .limit(1)
    )

    latest_status = db.scalar(
        select(SearchExecutionModel.status)
        .order_by(SearchExecutionModel.selected_at.desc())
        .limit(1)
    )

    total_processed = db.scalar(select(func.count()).select_from(JobModel)) or 0

    # Engine is considered active if we have successful syncs or recent activity
    engine_active = latest_status is not None

    return SystemStatusResponse(
        engine_active=engine_active,
        last_sync=last_sync,
        latest_execution_status=latest_status,
        total_processed=total_processed
    )
