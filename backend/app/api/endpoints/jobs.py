from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.db.database import get_db
from app.db.models.user import User
from app.db.models.job import JobModel
from app.api.deps import get_current_user
from app.schemas.job import JobResponse, PaginatedJobResponse

router = APIRouter()

@router.get(
    "",
    response_model=PaginatedJobResponse,
    summary="List normalized jobs",
    description="Returns a paginated list of normalized jobs, deterministically ordered by recency."
)
def list_jobs(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Base query
    base_query = select(JobModel)

    # Total count
    total = db.execute(select(func.count()).select_from(JobModel)).scalar_one()

    # Deterministic order: newest published/discovered first, then fallback to id
    # Nulls last for published_at to handle missing dates properly
    query = base_query.order_by(
        JobModel.published_at.desc().nulls_last(),
        JobModel.discovered_at.desc(),
        JobModel.id.desc()
    ).offset((page - 1) * size).limit(size)

    jobs = db.execute(query).scalars().all()

    return PaginatedJobResponse(
        items=list(jobs),
        total=total,
        page=page,
        size=size
    )

@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job details",
    description="Returns the details of a specific normalized job."
)
def get_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    job = db.execute(select(JobModel).where(JobModel.id == job_id)).scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    return job
