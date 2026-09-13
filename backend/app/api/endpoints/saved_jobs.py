from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
import logging

from app.db.database import get_db
from app.db.models.user import User
from app.db.models.job import JobModel
from app.db.models.saved_job import SavedJob
from app.api.deps import get_current_user
from app.schemas.saved_job import SavedJobCreate, SavedJobResponse, PaginatedSavedJobResponse

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "",
    response_model=SavedJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a job",
    description="Saves a normalized job for the authenticated user."
)
def save_job(
    saved_in: SavedJobCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    job = db.execute(select(JobModel).where(JobModel.id == saved_in.job_id)).scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    saved = SavedJob(user_id=current_user.id, job_id=saved_in.job_id)
    db.add(saved)

    try:
        db.commit()
        db.refresh(saved)
        saved.job = job
        return saved
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(SavedJob).where(SavedJob.user_id == current_user.id, SavedJob.job_id == saved_in.job_id)
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Job already saved"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

@router.get(
    "",
    response_model=PaginatedSavedJobResponse,
    summary="List saved jobs",
    description="Returns a paginated list of saved jobs for the authenticated user, ordered by saved_at."
)
def list_saved_jobs(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    base_query = select(SavedJob).where(SavedJob.user_id == current_user.id)
    total = db.execute(select(func.count()).select_from(base_query.subquery())).scalar_one()

    query = base_query.options(joinedload(SavedJob.job)).order_by(SavedJob.saved_at.desc(), SavedJob.id.desc()).offset((page - 1) * size).limit(size)
    saved_jobs = db.execute(query).scalars().all()

    return PaginatedSavedJobResponse(
        items=list(saved_jobs),
        total=total,
        page=page,
        size=size
    )

@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a saved job",
    description="Removes a saved job for the authenticated user."
)
def remove_saved_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    saved_job = db.execute(
        select(SavedJob).where(SavedJob.user_id == current_user.id, SavedJob.job_id == job_id)
    ).scalar_one_or_none()

    if saved_job:
        db.delete(saved_job)
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error removing saved job: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )

    return None
