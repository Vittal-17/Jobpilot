from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
import logging

from app.db.database import get_db
from app.db.models.user import User
from app.db.models.job import JobModel
from app.db.models.application import Application
from app.api.deps import get_current_user
from app.schemas.application import (
    ApplicationCreate,
    ApplicationUpdate,
    ApplicationResponse,
    PaginatedApplicationResponse
)

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an application",
    description="Creates a new application record for a job."
)
def create_application(
    app_in: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    job = db.execute(select(JobModel).where(JobModel.id == app_in.job_id)).scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    application = Application(user_id=current_user.id, job_id=app_in.job_id)
    db.add(application)

    try:
        db.commit()
        db.refresh(application)
        application.job = job
        return application
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(Application).where(Application.user_id == current_user.id, Application.job_id == app_in.job_id)
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Application already exists for this job"
            )
        logger.error("Unexpected IntegrityError when creating application")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating application: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

@router.get(
    "",
    response_model=PaginatedApplicationResponse,
    summary="List applications",
    description="Returns a paginated list of applications for the authenticated user, ordered by most recently updated."
)
def list_applications(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    base_query = select(Application).where(Application.user_id == current_user.id)
    total = db.execute(select(func.count()).select_from(base_query.subquery())).scalar_one()

    query = (
        base_query
        .options(joinedload(Application.job))
        .order_by(Application.updated_at.desc(), Application.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )

    applications = db.execute(query).scalars().all()

    return PaginatedApplicationResponse(
        items=list(applications),
        total=total,
        page=page,
        size=size
    )

@router.get(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Get application details",
    description="Returns a specific application for the authenticated user."
)
def get_application(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    application = db.execute(
        select(Application)
        .options(joinedload(Application.job))
        .where(Application.id == application_id, Application.user_id == current_user.id)
    ).scalar_one_or_none()

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found"
        )

    return application

@router.patch(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Update an application",
    description="Updates mutable fields (e.g., status) of a specific application for the authenticated user."
)
def update_application(
    application_id: int,
    app_update: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    application = db.execute(
        select(Application)
        .options(joinedload(Application.job))
        .where(Application.id == application_id, Application.user_id == current_user.id)
    ).scalar_one_or_none()

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found"
        )

    application.status = app_update.status

    try:
        db.commit()
        db.refresh(application)
        return application
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating application: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
