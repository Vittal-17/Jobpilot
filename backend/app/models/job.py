from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

class Job(BaseModel):
    # Required base fields
    title: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    source_job_id: str = Field(..., min_length=1)
    discovered_at: datetime

    @field_validator('title', 'company', 'source', 'source_job_id')
    @classmethod
    def not_empty_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty or whitespace only")
        return v.strip()

    # Optional fields (may not be available from all sources)
    location: Optional[str] = None
    remote: Optional[bool] = None
    employment_type: Optional[str] = None
    description: Optional[str] = None

    # Compensation (salary should not be negative)
    salary_min: Optional[int] = Field(default=None, ge=0)
    salary_max: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = None

    @field_validator('salary_max')
    @classmethod
    def check_salary_range(cls, v: Optional[int], info) -> Optional[int]:
        if v is not None and 'salary_min' in info.data and info.data['salary_min'] is not None:
            if info.data['salary_min'] > v:
                raise ValueError("salary_min cannot be greater than salary_max")
        return v

    # Source info
    url: Optional[HttpUrl] = None
    published_at: Optional[datetime] = None

    # Match score (0 to 100)
    match_score: Optional[int] = Field(default=None, ge=0, le=100)
