from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ListingStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


class ListingView(BaseModel):
    id: str
    seller_id: str
    title: str
    category: str
    price_pkr: float
    stock: int
    description: str
    status: str
    marketplace_listing_id: str | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "ListingView":
        return cls(
            id=row.id,
            seller_id=row.seller_id,
            title=row.title,
            category=row.category,
            price_pkr=row.price_pkr,
            stock=row.stock,
            description=row.description,
            status=row.status,
            marketplace_listing_id=row.marketplace_listing_id,
            created_at=row.created_at,
        )
