from datetime import datetime

from pydantic import BaseModel, Field


class OrderView(BaseModel):
    id: str
    user_id: str
    proposal_id: str | None = None
    status: str
    marketplace_order_id: str | None = None
    total_pkr: float
    items: list[dict] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "OrderView":
        return cls(
            id=row.id,
            user_id=row.user_id,
            proposal_id=row.proposal_id,
            status=row.status,
            marketplace_order_id=row.marketplace_order_id,
            total_pkr=row.total_pkr,
            items=row.items or [],
            created_at=row.created_at,
        )
