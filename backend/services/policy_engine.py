import time
from collections import defaultdict, deque

from backend.config import Settings, get_settings
from backend.exceptions import PolicyViolationError, RateLimitError


class PolicyEngine:
    """Permissions, spending limits, quantity limits and rate limits."""

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        self.default_spending_limit_pkr = s.default_spending_limit_pkr
        self.max_quantity_per_order = s.max_quantity_per_order
        self.rate_limit_per_minute = s.rate_limit_per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def check_rate_limit(self, user_id: str) -> None:
        now = time.monotonic()
        dq = self._hits[user_id]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.rate_limit_per_minute:
            raise RateLimitError("Too many messages — please wait a moment and try again.")
        dq.append(now)

    def check_quantity(self, quantity: int) -> None:
        if quantity < 1:
            raise PolicyViolationError("Quantity must be at least 1.")
        if quantity > self.max_quantity_per_order:
            raise PolicyViolationError(
                f"Maximum {self.max_quantity_per_order} units per order."
            )

    def check_spending(self, user, total_pkr: float) -> None:
        limit = getattr(user, "spending_limit_pkr", None) or self.default_spending_limit_pkr
        if total_pkr > limit:
            raise PolicyViolationError(
                f"This order totals PKR {total_pkr:,.0f}, which exceeds your spending "
                f"limit of PKR {limit:,.0f}."
            )

    def check_can_buy(self, user) -> None:
        # Phase 1: any registered user may act as a buyer.
        return None

    def check_can_sell(self, user) -> None:
        # Phase 1: any registered user may act as a seller.
        return None
