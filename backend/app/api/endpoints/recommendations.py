from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.db.database import get_db
from app.db.models.job import JobModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.schemas.match import PaginatedRecommendedJobResponse, RecommendedJobResponse, MatchResult, MatchReason

router = APIRouter()

@router.get("", response_model=PaginatedRecommendedJobResponse)
def list_recommendations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    offset = (page - 1) * size

    base_query = (
        select(RecommendationHistoryModel, JobModel)
        .join(JobModel, RecommendationHistoryModel.job_id == JobModel.id)
    )

    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    results = db.execute(
        base_query.order_by(RecommendationHistoryModel.recommended_at.desc())
        .offset(offset).limit(size)
    ).all()

    items = []
    for rec, job in results:
        # Legacy rows with NULL score/reasons must map to None.
        match_res = None
        if rec.score is not None or rec.reasons is not None:
            reasons = [MatchReason(**r) for r in rec.reasons] if rec.reasons else None
            match_res = MatchResult(job_id=job.id, score=rec.score, reasons=reasons)

        delivery_status = "DELIVERED" if rec.delivery_id else "PENDING"

        items.append(RecommendedJobResponse(
            job=job,
            match=match_res,
            recommended_at=rec.recommended_at,
            delivery_status=delivery_status
        ))

    return PaginatedRecommendedJobResponse(
        items=items,
        total=total,
        page=page,
        size=size
    )
