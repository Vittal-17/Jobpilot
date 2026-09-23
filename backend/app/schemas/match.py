from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from app.schemas.job import JobResponse

class RecommendationPreferences(BaseModel):
    """
    Extracted preferences for matching against a job.
    Uses existing UserProfile fields.

    Notes:
    - remote_preference only supports 'remote' and 'onsite' accurately
      due to the boolean nature of JobModel.remote.
    - experience_years is matched using a deterministic title-based heuristic.
    """
    preferred_roles: Optional[str] = None
    skills: Optional[str] = None
    preferred_locations: Optional[str] = None
    remote_preference: Optional[str] = None
    experience_years: Optional[int] = Field(None, ge=0)

class MatchReason(BaseModel):
    code: str
    message: str

class MatchResult(BaseModel):
    job_id: int
    score: Optional[int] = Field(None, ge=0, le=100)
    reasons: Optional[List[MatchReason]] = None

class RecommendedJobResponse(BaseModel):
    job: JobResponse
    match: Optional[MatchResult] = None
    recommended_at: datetime
    delivery_status: Optional[str] = None

class PaginatedRecommendedJobResponse(BaseModel):
    items: List[RecommendedJobResponse]
    total: int
    page: int
    size: int
