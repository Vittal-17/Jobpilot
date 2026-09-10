from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.providers.types import ProviderName
from app.providers.registry import (
    PROVIDER_PRIORITY,
    create_provider,
    is_provider_enabled,
)
from app.providers.exceptions import ProviderConfigurationError
from app.services.quota_policy import get_provider_policy


logger = logging.getLogger(__name__)
ROUTING_POLICY_VERSION = "v1"


class ProviderNotConfigured(Exception):
    pass


class ProviderUnavailable(Exception):
    pass


class ProviderQuotaExhausted(Exception):
    pass


class ProviderRoutingUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ProviderCapacity:
    provider: ProviderName
    minute_remaining: int | None
    daily_remaining: int | None
    lifetime_remaining: int | None

    @property
    def available(self) -> bool:
        return all(
            remaining is None or remaining > 0
            for remaining in (
                self.minute_remaining,
                self.daily_remaining,
                self.lifetime_remaining,
            )
        )

    @property
    def constrained_remaining(self) -> int:
        remaining = [
            value
            for value in (
                self.minute_remaining,
                self.daily_remaining,
                self.lifetime_remaining,
            )
            if value is not None
        ]
        return min(remaining) if remaining else 2**63 - 1


@dataclass(frozen=True)
class ProviderSelectionResult:
    provider: ProviderName
    reason: str
    policy_version: str = ROUTING_POLICY_VERSION


def get_provider_capacity(
    db: Session,
    provider_name: ProviderName,
    reference_time: datetime | None = None,
) -> ProviderCapacity:
    policy = get_provider_policy(provider_name.value)
    limits = {
        "minute": policy.get_effective_limit("minute"),
        "daily": policy.get_effective_limit("daily"),
        "lifetime": policy.get_effective_limit("lifetime"),
    }
    now = reference_time or datetime.now(timezone.utc)
    usage_queries = {
        "minute": (
            "SELECT request_count FROM provider_minute_usage "
            "WHERE provider_name = :provider AND usage_minute = :bucket",
            now.replace(second=0, microsecond=0),
        ),
        "daily": (
            "SELECT request_count FROM provider_usage "
            "WHERE provider_name = :provider AND usage_date = :bucket",
            now.date(),
        ),
        "lifetime": (
            "SELECT lifetime_count FROM provider_state WHERE provider_name = :provider",
            None,
        ),
    }
    remaining: dict[str, int | None] = {}
    try:
        for dimension, limit in limits.items():
            if limit is None:
                remaining[dimension] = None
                continue
            sql, bucket = usage_queries[dimension]
            params = {"provider": provider_name.value}
            if bucket is not None:
                params["bucket"] = bucket
            usage = db.execute(text(sql), params).scalar() or 0
            remaining[dimension] = limit - usage
    except Exception as exc:
        logger.exception("Provider routing quota read failed")
        raise ProviderRoutingUnavailable("Provider routing is temporarily unavailable") from exc

    return ProviderCapacity(
        provider=provider_name,
        minute_remaining=remaining["minute"],
        daily_remaining=remaining["daily"],
        lifetime_remaining=remaining["lifetime"],
    )


def route_provider(
    db: Session,
    reference_time: datetime | None = None,
) -> ProviderSelectionResult:
    configured: list[tuple[int, ProviderCapacity]] = []
    enabled_count = 0
    configured_count = 0
    unavailable_count = 0
    for priority, provider_name in enumerate(PROVIDER_PRIORITY):
        if not is_provider_enabled(provider_name):
            continue
        enabled_count += 1
        try:
            provider = create_provider(provider_name)
            provider.validate_config()
        except ProviderConfigurationError:
            continue
        except ProviderUnavailable:
            unavailable_count += 1
            logger.warning(
                "Provider construction failed provider_name=%s", provider_name.value
            )
            continue

        configured_count += 1
        capacity = get_provider_capacity(db, provider_name, reference_time)
        if capacity.available:
            configured.append((priority, capacity))

    if not configured:
        if enabled_count == 0:
            raise ProviderUnavailable("No providers are enabled")
        if configured_count == 0:
            if unavailable_count:
                raise ProviderUnavailable("All configured providers are unavailable")
            raise ProviderNotConfigured("No providers are configured")
        raise ProviderQuotaExhausted("All configured providers are quota exhausted")

    selected = rank_provider_capacities(configured)
    return ProviderSelectionResult(
        provider=selected.provider,
        reason="provider_priority_then_remaining_capacity",
    )


def rank_provider_capacities(
    providers: list[tuple[int, ProviderCapacity]],
) -> ProviderCapacity:
    return min(
        providers,
        key=lambda item: (
            item[0],
            -item[1].constrained_remaining,
            item[1].provider.value,
        ),
    )[1]
