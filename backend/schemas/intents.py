from enum import Enum

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    SEARCH = "search"
    BUY = "buy"
    APPROVE = "approve"
    REJECT = "reject"
    ORDER_STATUS = "order_status"
    SELLER_LISTING = "seller_listing"
    STOCK_CHECK = "stock_check"
    SALES_SUMMARY = "sales_summary"
    HELP = "help"
    CLARIFY = "clarify"


class Intent(BaseModel):
    """Structured intent. Phase 1 fills this with rules; Phase 3 fills it with an LLM."""

    type: IntentType
    mode: str = "buyer"  # buyer | seller | any
    params: dict = Field(default_factory=dict)
