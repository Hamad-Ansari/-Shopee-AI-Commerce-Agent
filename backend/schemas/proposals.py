from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    PLACE_ORDER = "place_order"
    PUBLISH_LISTING = "publish_listing"


class ProposalStatus(str, Enum):
    REQUESTED = "REQUESTED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVALIDATED = "REVALIDATED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ProposalItem(BaseModel):
    product_id: str
    title: str
    quantity: int
    unit_price_pkr: float
    seller: str


class ProposalView(BaseModel):
    id: str
    user_id: str
    action_type: str
    status: str
    version: int
    items: list[ProposalItem] = Field(default_factory=list)
    shipping_fee_pkr: float = 0.0
    total_pkr: float = 0.0
    payload: dict = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "ProposalView":
        items = row.items or []
        return cls(
            id=row.id,
            user_id=row.user_id,
            action_type=row.action_type,
            status=row.status,
            version=row.version,
            items=[ProposalItem(**i) for i in items],
            shipping_fee_pkr=row.shipping_fee_pkr,
            total_pkr=row.total_pkr,
            payload={
                "items": items,
                "shipping_fee_pkr": row.shipping_fee_pkr,
                "total_pkr": row.total_pkr,
            },
            created_at=row.created_at,
        )
