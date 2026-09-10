from pydantic import BaseModel

class SearchCandidate(BaseModel):
    candidate_id: str  # Deterministic identifier: "{role_id}::{location_id}"
    role_id: str
    location_id: str
    role_canonical: str
    location_canonical: str
    priority: int
    tier: int
