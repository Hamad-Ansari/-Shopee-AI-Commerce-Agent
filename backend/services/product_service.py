import json
from pathlib import Path

from backend.config import get_settings
from backend.exceptions import PolicyViolationError, ProductNotFoundError


class ProductService:
    """Mock catalog backed by data/mock_products.json (the 'marketplace' in Phase 1)."""

    def __init__(self, catalog_path: str | None = None):
        settings = get_settings()
        p = Path(catalog_path or settings.mock_catalog_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[2] / p  # project root
        self.catalog_path = p
        self.reload()

    def reload(self) -> None:
        self._products = json.loads(self.catalog_path.read_text(encoding="utf-8"))["products"]
        self._by_id = {p["id"]: p for p in self._products}

    def search(
        self,
        query: str = "",
        max_price_pkr: float | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        tokens = [t for t in query.lower().split() if t]
        results = []
        for p in self._products:
            hay = f"{p['title']} {p['category']} {p['seller']}".lower()
            if tokens and not all(t in hay for t in tokens):
                continue
            if category and p["category"].lower() != category.lower():
                continue
            if max_price_pkr is not None and p["price_pkr"] > max_price_pkr:
                continue
            results.append(p)
        results.sort(key=lambda p: (-p["rating"], p["price_pkr"]))
        return results[:limit]

    def get(self, product_id: str) -> dict:
        p = self._by_id.get(product_id)
        if not p:
            raise ProductNotFoundError(f"Product '{product_id}' not found.")
        return p

    def check_availability(self, product_id: str, quantity: int) -> dict:
        p = self.get(product_id)
        if p["stock"] < quantity:
            raise PolicyViolationError(
                f"Only {p['stock']} unit(s) of \"{p['title']}\" left in stock."
            )
        return p

    def set_price(self, product_id: str, new_price: float) -> None:
        """Test/demo hook: simulate a marketplace price change."""
        self.get(product_id)["price_pkr"] = float(new_price)

    def all(self) -> list[dict]:
        return list(self._products)
