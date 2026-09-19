from pydantic import BaseModel, Field
from typing import List

class SearchCandidate(BaseModel):
    candidate_id: str  # Deterministic identifier: "{role_id}::{location_id}"
    role_id: str
    location_id: str
    role_canonical: str
    location_canonical: str
    priority: int
    tier: int
    query_variant: str = ""
    retrieval_location: str | None = None
    variants: List[str] = Field(default_factory=list)
