from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db, get_current_user
from app.db.models import UserSearch, User
from app.schemas.search import UserSearchCreate, UserSearchUpdate, UserSearchResponse, PaginatedUserSearchResponse

router = APIRouter()

@router.post("", response_model=UserSearchResponse, status_code=status.HTTP_201_CREATED)
def create_search(
    data: UserSearchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = data.query.strip() if data.query else None
    location = data.location.strip() if data.location else None

    if not query and not location:
        raise HTTPException(status_code=422, detail="Either query or location must be provided")

    search = UserSearch(
        user_id=current_user.id,
        query=query,
        location=location,
        remote_only=data.remote_only,
        enabled=data.enabled
    )
    db.add(search)
    try:
        db.commit()
        db.refresh(search)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid search configuration")

    return search

@router.get("", response_model=PaginatedUserSearchResponse)
def list_searches(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    offset = (page - 1) * size
    stmt = select(UserSearch).where(UserSearch.user_id == current_user.id).order_by(UserSearch.created_at.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.offset(offset).limit(size)).all()

    return PaginatedUserSearchResponse(
        items=list(items),
        total=total,
        page=page,
        size=size
    )

@router.get("/{search_id}", response_model=UserSearchResponse)
def get_search(
    search_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    search = db.scalar(select(UserSearch).where(UserSearch.id == search_id, UserSearch.user_id == current_user.id))
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    return search

@router.patch("/{search_id}", response_model=UserSearchResponse)
def update_search(
    search_id: int,
    data: UserSearchUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    search = db.scalar(select(UserSearch).where(UserSearch.id == search_id, UserSearch.user_id == current_user.id))
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    q = data.query if data.query is not None else search.query
    l = data.location if data.location is not None else search.location
    if not q and not l:
        raise HTTPException(status_code=422, detail="Either query or location must be provided")

    if data.query is not None:
        search.query = data.query if data.query else None
    if data.location is not None:
        search.location = data.location if data.location else None
    if data.remote_only is not None:
        search.remote_only = data.remote_only
    if data.enabled is not None:
        search.enabled = data.enabled

    try:
        db.commit()
        db.refresh(search)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid search configuration")

    return search

@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_search(
    search_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    search = db.scalar(select(UserSearch).where(UserSearch.id == search_id, UserSearch.user_id == current_user.id))
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    try:
        db.delete(search)
        db.commit()
    except Exception:
        db.rollback()
        raise
