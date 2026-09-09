from typing import Optional
from pydantic import BaseModel, Field
from typing import Literal
from app.core.config import settings

class QuotaDimensions(BaseModel):
    minute: Optional[int] = None
    daily: Optional[int] = None
    weekly: Optional[int] = None
    monthly: Optional[int] = None
    lifetime: Optional[int] = None

class ProviderQuotaPolicy(BaseModel):
    provider_ceiling: QuotaDimensions
    account_ceiling: QuotaDimensions = Field(default_factory=QuotaDimensions)
    safety_budget: QuotaDimensions = Field(default_factory=QuotaDimensions)

    def get_effective_limit(self, dimension: Literal['minute', 'daily', 'weekly', 'monthly', 'lifetime']) -> Optional[int]:
        limits = [
            getattr(self.provider_ceiling, dimension),
            getattr(self.account_ceiling, dimension),
            getattr(self.safety_budget, dimension)
        ]
        valid_limits = [lim for lim in limits if lim is not None]
        return min(valid_limits) if valid_limits else None

def get_provider_policy(provider_name: str) -> ProviderQuotaPolicy:
    if provider_name == "adzuna":
        return ProviderQuotaPolicy(
            provider_ceiling=QuotaDimensions(minute=25, daily=250, weekly=1000, monthly=2500),
            account_ceiling=QuotaDimensions(daily=settings.adzuna_account_limit_daily),
            safety_budget=QuotaDimensions(daily=settings.adzuna_safety_budget_daily)
        )
    elif provider_name == "jooble":
        return ProviderQuotaPolicy(
            provider_ceiling=QuotaDimensions(lifetime=500),
            account_ceiling=QuotaDimensions(lifetime=settings.jooble_account_limit_lifetime),
            safety_budget=QuotaDimensions(daily=settings.jooble_safety_budget_daily, lifetime=settings.jooble_safety_budget_lifetime)
        )
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
