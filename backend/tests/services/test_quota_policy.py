import pytest
from app.services.quota_policy import get_provider_policy

def test_adzuna_provider_ceilings():
    policy = get_provider_policy("adzuna")
    assert policy.provider_ceiling.minute == 25
    assert policy.provider_ceiling.daily == 250
    assert policy.provider_ceiling.weekly == 1000
    assert policy.provider_ceiling.monthly == 2500

def test_jooble_provider_ceilings():
    policy = get_provider_policy("jooble")
    assert policy.provider_ceiling.lifetime == 500

def test_jobpilot_safety_budget_differs_from_provider_ceiling():
    policy = get_provider_policy("adzuna")
    # Provider is 250, but safety budget is 25
    assert policy.provider_ceiling.daily == 250
    assert policy.safety_budget.daily == 25
    assert policy.get_effective_limit('daily') == 25

def test_jooble_safety_budget_independent_of_provider_facts():
    policy = get_provider_policy("jooble")
    # Provider does NOT document a daily limit, so it's None.
    assert policy.provider_ceiling.daily is None
    # JobPilot actively applies a daily safety budget of 2.
    assert policy.safety_budget.daily == 2
    assert policy.get_effective_limit('daily') == 2

    # Provider documents 500 lifetime, and safety budget enforces it too.
    assert policy.provider_ceiling.lifetime == 500
    assert policy.get_effective_limit('lifetime') == 500

def test_effective_budget_calculation_minimum():
    policy = get_provider_policy("adzuna")
    # Setting an account ceiling tighter than safety budget
    policy.account_ceiling.daily = 10
    assert policy.get_effective_limit('daily') == 10

    # Setting an account ceiling looser than safety budget
    policy.account_ceiling.daily = 100
    assert policy.get_effective_limit('daily') == 25

def test_missing_quota_dimensions_ignored():
    policy = get_provider_policy("jooble")
    # Jooble has no minute limit
    assert policy.get_effective_limit('minute') is None

def test_unknown_provider():
    with pytest.raises(ValueError):
        get_provider_policy("unknown_provider")


def test_effective_budget_calculation_weekly_and_monthly():
    policy = get_provider_policy("adzuna")
    assert policy.get_effective_limit('weekly') == 1000
    assert policy.get_effective_limit('monthly') == 2500

    # Override with tighter account ceiling
    policy.account_ceiling.weekly = 500
    policy.account_ceiling.monthly = 1200
    assert policy.get_effective_limit('weekly') == 500
    assert policy.get_effective_limit('monthly') == 1200

    # Override with tighter safety budget
    policy.safety_budget.weekly = 200
    policy.safety_budget.monthly = 800
    assert policy.get_effective_limit('weekly') == 200
    assert policy.get_effective_limit('monthly') == 800


def test_firecrawl_policy_defaults():
    from app.core.config import settings
    orig_cap = settings.firecrawl_monthly_automation_cap
    orig_budget = settings.firecrawl_monthly_budget
    orig_reserve = settings.firecrawl_reserved_credits
    try:
        settings.firecrawl_monthly_automation_cap = 900
        settings.firecrawl_monthly_budget = 1000
        settings.firecrawl_reserved_credits = 100

        policy_fc = get_provider_policy("firecrawl")
        policy_fcm = get_provider_policy("firecrawl_monthly")

        for p in (policy_fc, policy_fcm):
            assert p.provider_ceiling.monthly == 1000
            assert p.account_ceiling.monthly == 1000
            assert p.safety_budget.monthly == 900
            assert p.get_effective_limit("monthly") == 900
            assert p.get_effective_limit("daily") is None
            assert p.get_effective_limit("minute") is None
            assert p.get_effective_limit("lifetime") is None
    finally:
        settings.firecrawl_monthly_automation_cap = orig_cap
        settings.firecrawl_monthly_budget = orig_budget
        settings.firecrawl_reserved_credits = orig_reserve


def test_firecrawl_policy_tight_budget():
    from app.core.config import settings
    orig_cap = settings.firecrawl_monthly_automation_cap
    orig_budget = settings.firecrawl_monthly_budget
    try:
        settings.firecrawl_monthly_automation_cap = 900
        settings.firecrawl_monthly_budget = 5

        policy = get_provider_policy("firecrawl")
        assert policy.provider_ceiling.monthly == 5
        assert policy.safety_budget.monthly == 5
        assert policy.get_effective_limit("monthly") == 5
    finally:
        settings.firecrawl_monthly_automation_cap = orig_cap
        settings.firecrawl_monthly_budget = orig_budget
