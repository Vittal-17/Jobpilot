from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, model_validator

class UserSearchCreate(BaseModel):
    query: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    remote_only: bool = False
    enabled: bool = True

    @model_validator(mode='after')
    def check_not_empty(self) -> 'UserSearchCreate':
        q = self.query.strip() if self.query else ""
        l = self.location.strip() if self.location else ""
        if not q and not l:
            raise ValueError("Either query or location must be provided")
        return self

class UserSearchUpdate(BaseModel):
    query: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    remote_only: Optional[bool] = None
    enabled: Optional[bool] = None

    @model_validator(mode='after')
    def strip_whitespace(self) -> 'UserSearchUpdate':
        if self.query is not None:
            self.query = self.query.strip()
        if self.location is not None:
            self.location = self.location.strip()
        return self

class UserSearchResponse(BaseModel):
    id: int
    query: Optional[str] = None
    location: Optional[str] = None
    remote_only: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class PaginatedUserSearchResponse(BaseModel):
    items: list[UserSearchResponse]
    total: int
    page: int
    size: int
