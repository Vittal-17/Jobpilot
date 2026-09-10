from app.core.config import settings
from app.providers.adzuna import AdzunaProvider
from app.providers.base import JobProvider
from app.providers.types import ProviderName
from app.providers.jooble import JoobleProvider


PROVIDER_PRIORITY: tuple[ProviderName, ...] = (
    ProviderName.ADZUNA,
    ProviderName.JOOBLE,
)


def is_provider_enabled(provider_name: ProviderName) -> bool:
    return {
        ProviderName.ADZUNA: settings.adzuna_enabled,
        ProviderName.JOOBLE: settings.jooble_enabled,
    }[provider_name]


def create_provider(provider_name: ProviderName) -> JobProvider:
    factories = {
        ProviderName.ADZUNA: AdzunaProvider,
        ProviderName.JOOBLE: JoobleProvider,
    }
    return factories[provider_name]()
