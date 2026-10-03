from typing import Any
from pydantic import BaseModel

class FirecrawlLastOperation(BaseModel):
    operation: str
    credits: int
    status: str
    timestamp: str | None = None

class FirecrawlTelemetryResponse(BaseModel):
    monthly_cap: int
    monthly_used: int
    monthly_remaining: int
    reserved: int
    discovery_used: int
    enrichment_used: int
    quota_denied: int
    last_operation: FirecrawlLastOperation | dict[str, Any] | None = None
    is_exhausted: bool
