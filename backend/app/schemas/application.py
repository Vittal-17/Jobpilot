from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict
from app.schemas.job import JobResponse

ApplicationStatusType = Literal["applied", "interviewing", "rejected", "offer", "withdrawn"]

class ApplicationCreate(BaseModel):
    job_id: int

class ApplicationUpdate(BaseModel):
    status: ApplicationStatusType

class ApplicationResponse(BaseModel):
    id: int
    status: str
    created_at: datetime
    updated_at: datetime
    job: JobResponse

    model_config = ConfigDict(from_attributes=True)

class PaginatedApplicationResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int
    page: int
    size: int
