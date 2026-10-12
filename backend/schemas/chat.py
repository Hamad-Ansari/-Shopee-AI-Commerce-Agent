from pydantic import BaseModel, Field

from backend.schemas.listings import ListingView
from backend.schemas.orders import OrderView
from backend.schemas.proposals import ProposalView

PRODUCT_CARD_FIELDS = [
    "id",
    "title",
    "category",
    "price_pkr",
    "seller",
    "rating",
    "reviews_count",
    "stock",
    "delivery_days",
    "shipping_fee_pkr",
]


class ProductCard(BaseModel):
    """A product summary shown to the user. Deliberately excludes the free-text
    description: product text is untrusted DATA and must not be echoed as instructions."""

    id: str
    title: str
    category: str
    price_pkr: float
    seller: str
    rating: float
    reviews_count: int
    stock: int
    delivery_days: int
    shipping_fee_pkr: float


def product_card(p: dict) -> ProductCard:
    return ProductCard(**{k: p[k] for k in PRODUCT_CARD_FIELDS})


class ChatRequest(BaseModel):
    user_id: str = ""  # optional; must match the authenticated user when provided
    message: str = Field(min_length=1)
    name: str = ""
    role: str = ""  # buyer | seller (optional; stored on first use)


class ChatResponse(BaseModel):
    user_id: str = ""
    message: str
    products: list[ProductCard] = Field(default_factory=list)
    proposal: ProposalView | None = None
    order: OrderView | None = None
    listing: ListingView | None = None
    needs_approval: bool = False
    action: str = ""


class ActionOutcome(BaseModel):
    """Result of an approve / reject / status-check action."""

    ok: bool = True
    message: str
    proposal: ProposalView | None = None
    order: OrderView | None = None
    listing: ListingView | None = None
    needs_approval: bool = False
