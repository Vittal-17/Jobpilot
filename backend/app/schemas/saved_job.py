from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.schemas.job import JobResponse

class SavedJobCreate(BaseModel):
    job_id: int

class SavedJobResponse(BaseModel):
    id: int
    saved_at: datetime
    job: JobResponse

    model_config = ConfigDict(from_attributes=True)

class PaginatedSavedJobResponse(BaseModel):
    items: list[SavedJobResponse]
    total: int
    page: int
    size: int
