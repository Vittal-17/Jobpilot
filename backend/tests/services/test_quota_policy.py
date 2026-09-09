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
