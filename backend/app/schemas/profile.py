from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

class ProfileUpdate(BaseModel):
    headline: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    experience_years: Optional[int] = Field(None, ge=0)
    skills: Optional[str] = None
    preferred_roles: Optional[str] = None
    preferred_locations: Optional[str] = None
    remote_preference: Optional[str] = Field(None, max_length=50)

class ProfileResponse(BaseModel):
    headline: Optional[str] = None
    location: Optional[str] = None
    experience_years: Optional[int] = None
    skills: Optional[str] = None
    preferred_roles: Optional[str] = None
    preferred_locations: Optional[str] = None
    remote_preference: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
