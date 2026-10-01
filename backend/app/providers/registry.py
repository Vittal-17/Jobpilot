from app.core.config import settings
from app.providers.adzuna import AdzunaProvider
from app.providers.base import JobProvider
from app.providers.types import ProviderName
from app.providers.jooble import JoobleProvider
from app.providers.firecrawl import FirecrawlProvider


# Generic Search Cycle priority for continuous polling (Adzuna -> Jooble).
# Firecrawl is intentionally excluded from continuous search-cycle routing to protect
# its shared 900-credit monthly automation pool from high-frequency polling exhaustion.
# Firecrawl discovery is invoked directly and conservatively by its dedicated Phase 4
# discovery workflow (JP___Firecrawl_Discovery) via create_provider(ProviderName.FIRECRAWL).
PROVIDER_PRIORITY: tuple[ProviderName, ...] = (
    ProviderName.ADZUNA,
    ProviderName.JOOBLE,
)


def is_provider_enabled(provider_name: ProviderName) -> bool:
    return {
        ProviderName.ADZUNA: settings.adzuna_enabled,
        ProviderName.JOOBLE: settings.jooble_enabled,
        ProviderName.FIRECRAWL: getattr(settings, "firecrawl_discovery_enabled", False),
    }[provider_name]


def create_provider(provider_name: ProviderName) -> JobProvider:
    factories = {
        ProviderName.ADZUNA: AdzunaProvider,
        ProviderName.JOOBLE: JoobleProvider,
        ProviderName.FIRECRAWL: FirecrawlProvider,
    }
    return factories[provider_name]()
