from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    source: str
    location: Optional[str] = None
    remote: Optional[bool] = None
    employment_type: Optional[str] = None
    description: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    discovered_at: datetime

    model_config = ConfigDict(from_attributes=True)

class PaginatedJobResponse(BaseModel):
    items: list[JobResponse]
    total: int
    page: int
    size: int
