from typing import Any
from pydantic import BaseModel, Field

class FirecrawlLastOperation(BaseModel):
    operation: str
    credits: int
    status: str
    timestamp: str | None = None

class FirecrawlStageCounters(BaseModel):
    candidates_found: int | None = None
    validated_individual: int | None = None
    enriched: int | None = None
    eligible: int | None = None
    recommended: int | None = None

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
    stage_counters: FirecrawlStageCounters | None = None

class FirecrawlFunnelResponse(BaseModel):
    execution_id: int | None = None
    jobs_ingested: int = 0
    jobs_snippet: int = 0
    jobs_full_description: int = 0
    enrichments_pending: int = 0
    enrichments_in_progress: int = 0
    enrichments_success: int = 0
    enrichments_unsupported: int = 0
    enrichments_failure: int = 0
    listings_rejected_at_enrichment: int = 0
    fresher_eligible: int = 0
    recommendations_total: int = 0
    recommendations_delivered: int = 0
    recommendations_pending: int = 0
    rejections_by_reason: dict[str, int] = Field(default_factory=dict)
