from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.exceptions import PolicyViolationError, RateLimitError
from backend.services.policy_engine import PolicyEngine


def test_spending_limit():
    pe = PolicyEngine(Settings())
    user = SimpleNamespace(spending_limit_pkr=1000)
    pe.check_spending(user, 999)  # within limit: ok
    with pytest.raises(PolicyViolationError):
        pe.check_spending(user, 1001)


def test_quantity_limits():
    pe = PolicyEngine(Settings())
    pe.check_quantity(1)
    pe.check_quantity(10)
    with pytest.raises(PolicyViolationError):
        pe.check_quantity(0)
    with pytest.raises(PolicyViolationError):
        pe.check_quantity(11)


def test_rate_limit_is_per_user():
    pe = PolicyEngine(Settings(rate_limit_per_minute=2))
    pe.check_rate_limit("u1")
    pe.check_rate_limit("u1")
    with pytest.raises(RateLimitError):
        pe.check_rate_limit("u1")
    pe.check_rate_limit("u2")  # independent budget
