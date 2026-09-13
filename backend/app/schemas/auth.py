from pydantic import BaseModel, Field
from datetime import datetime

class UserCreate(BaseModel):
    email: str = Field(..., pattern=r".+@.+")
    password: str = Field(..., min_length=8)
    display_name: str | None = None

class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
