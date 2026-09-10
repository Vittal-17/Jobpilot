from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.providers.types import ProviderName
from app.providers.exceptions import ProviderConfigurationError
from app.services.provider_router import (
    ProviderCapacity,
    ProviderNotConfigured,
    ProviderQuotaExhausted,
    ProviderRoutingUnavailable,
    ProviderSelectionResult,
    ProviderUnavailable,
    rank_provider_capacities,
    route_provider,
)


class ConfiguredProvider:
    def validate_config(self):
        return None


class MisconfiguredProvider:
    def validate_config(self):
        raise ProviderConfigurationError("missing")


class BrokenProvider:
    def validate_config(self):
        raise RuntimeError("broken")


def capacity(provider, remaining):
    return ProviderCapacity(provider, remaining, remaining, remaining)


def test_provider_package_exports_are_authoritative():
    import app.providers as providers

    assert providers.__all__ == [
        "JobProvider",
        "AdzunaProvider",
        "JoobleProvider",
        "ProviderName",
    ]
    assert all(hasattr(providers, name) for name in providers.__all__)


def test_priority_is_explicit_and_deterministic(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr("app.services.provider_router.create_provider", lambda _: ConfiguredProvider())
    monkeypatch.setattr(
        "app.services.provider_router.get_provider_capacity",
        lambda db, name, now: capacity(name, 1 if name == ProviderName.ADZUNA else 100),
    )

    first = route_provider(MagicMock(), datetime(2026, 1, 1, tzinfo=timezone.utc))
    second = route_provider(MagicMock(), datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert first == second
    assert first == ProviderSelectionResult(
        provider=ProviderName.ADZUNA,
        reason="provider_priority_then_remaining_capacity",
    )


def test_exhausted_priority_provider_selects_next_capacity(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr("app.services.provider_router.create_provider", lambda _: ConfiguredProvider())
    monkeypatch.setattr(
        "app.services.provider_router.get_provider_capacity",
        lambda db, name, now: capacity(name, 0 if name == ProviderName.ADZUNA else 1),
    )
    assert route_provider(MagicMock()).provider == ProviderName.JOOBLE


def test_equal_priority_prefers_capacity_then_name():
    adzuna = capacity(ProviderName.ADZUNA, 2)
    jooble = capacity(ProviderName.JOOBLE, 5)
    assert rank_provider_capacities([(0, adzuna), (0, jooble)]) == jooble

    tied_jooble = capacity(ProviderName.JOOBLE, 2)
    assert rank_provider_capacities([(0, tied_jooble), (0, adzuna)]) == adzuna


def test_misconfigured_provider_does_not_block_usable_provider(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr(
        "app.services.provider_router.create_provider",
        lambda name: MisconfiguredProvider()
        if name == ProviderName.ADZUNA
        else ConfiguredProvider(),
    )
    monkeypatch.setattr(
        "app.services.provider_router.get_provider_capacity",
        lambda db, name, now: capacity(name, 1),
    )
    assert route_provider(MagicMock()).provider == ProviderName.JOOBLE


def test_unavailable_provider_does_not_block_usable_provider(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr(
        "app.services.provider_router.create_provider",
        lambda name: (_ for _ in ()).throw(ProviderUnavailable("temporarily unavailable"))
        if name == ProviderName.ADZUNA
        else ConfiguredProvider(),
    )
    monkeypatch.setattr(
        "app.services.provider_router.get_provider_capacity",
        lambda db, name, now: capacity(name, 1),
    )
    assert route_provider(MagicMock()).provider == ProviderName.JOOBLE


def test_unexpected_provider_failure_propagates(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr(
        "app.services.provider_router.create_provider",
        lambda name: BrokenProvider() if name == ProviderName.ADZUNA else ConfiguredProvider(),
    )
    with pytest.raises(RuntimeError, match="broken"):
        route_provider(MagicMock())


def test_all_disabled_fails_closed(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: False)
    with pytest.raises(ProviderUnavailable, match="enabled"):
        route_provider(MagicMock())


def test_all_configured_providers_exhausted(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr("app.services.provider_router.create_provider", lambda _: ConfiguredProvider())
    monkeypatch.setattr(
        "app.services.provider_router.get_provider_capacity",
        lambda db, name, now: capacity(name, 0),
    )
    with pytest.raises(ProviderQuotaExhausted):
        route_provider(MagicMock())


def test_quota_read_failure_is_not_mapped_to_no_provider(monkeypatch):
    monkeypatch.setattr("app.services.provider_router.is_provider_enabled", lambda _: True)
    monkeypatch.setattr("app.services.provider_router.create_provider", lambda _: ConfiguredProvider())
    monkeypatch.setattr(
        "app.services.provider_router.get_provider_capacity",
        MagicMock(side_effect=ProviderRoutingUnavailable("db")),
    )
    with pytest.raises(ProviderRoutingUnavailable):
        route_provider(MagicMock())
