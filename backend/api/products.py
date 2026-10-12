from fastapi import APIRouter, HTTPException

from backend.dependencies import get_product_service
from backend.exceptions import ProductNotFoundError
from backend.schemas.chat import product_card

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/search")
def search_products(q: str = "", max_price_pkr: float | None = None, category: str | None = None, limit: int = 10):
    items = get_product_service().search(
        query=q, max_price_pkr=max_price_pkr, category=category, limit=limit
    )
    return {"products": [product_card(p) for p in items]}


@router.get("/{product_id}")
def get_product(product_id: str):
    try:
        p = get_product_service().get(product_id)
    except ProductNotFoundError:
        raise HTTPException(status_code=404, detail="Product not found")
    return product_card(p)
