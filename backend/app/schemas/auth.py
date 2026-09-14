from pydantic import BaseModel, Field
from datetime import datetime

class UserCreate(BaseModel):
    email: str = Field(..., pattern=r".+@.+")
    password: str = Field(..., min_length=8)
    display_name: str | None = None

class LoginRequest(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str | None = None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}
