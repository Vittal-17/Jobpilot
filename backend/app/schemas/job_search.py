from pydantic import BaseModel, Field, field_validator

class JobSearchQuery(BaseModel):
    keywords: str = Field(..., min_length=1, max_length=100)
    location: str = Field(..., min_length=1, max_length=100)
    radius_km: int | None = Field(None, ge=0, le=200)
    page: int = Field(1, ge=1, le=1000)
    page_size: int = Field(20, ge=1, le=100)

    @field_validator('keywords', 'location')
    @classmethod
    def not_empty_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty or whitespace only")
        return v.strip()


from pydantic import ConfigDict

class CanonicalSearchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: str = Field(..., min_length=1)
    keywords: str = Field(..., min_length=1, max_length=100)
    location_id: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1, max_length=100)
    priority: int | None = Field(None)

    @field_validator('role_id', 'keywords', 'location_id', 'location')
    @classmethod
    def not_empty_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty or whitespace only")
        return v.strip()

class IngestionResult(BaseModel):
    provider: str
    fetched: int = 0
    created: int = 0
    duplicates: int = 0
    invalid: int = 0
    failed: int = 0
