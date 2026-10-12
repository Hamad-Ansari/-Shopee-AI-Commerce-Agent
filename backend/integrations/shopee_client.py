import hashlib
import hmac
import time
from abc import ABC, abstractmethod

import httpx

from backend.exceptions import IntegrationUnavailableError, MarketplaceError, MarketplaceTimeoutError


class ShopeeClient(ABC):
    """Interface for the marketplace. The LLM/agent never talks to this directly —
    only the executor node does, after a valid approval."""

    is_authorized: bool = False

    @abstractmethod
    def place_order(self, *, idempotency_key: str, product_id: str, quantity: int, buyer_id: str) -> dict:
        ...

    @abstractmethod
    def get_order_status(self, identifier: str) -> dict | None:
        ...

    @abstractmethod
    def publish_listing(self, *, idempotency_key: str, listing: dict) -> dict:
        ...

    @abstractmethod
    def get_listing_status(self, listing_id: str) -> dict | None:
        ...


class MockShopeeClient(ShopeeClient):
    """Default Phase 1 client: an in-memory marketplace.

    - Honours idempotency keys (same key -> same stored result, never a duplicate).
    - simulate_timeout_for_product: the order IS created server-side but the response
      is 'lost' (raises MarketplaceTimeoutError), to exercise the UNKNOWN -> status-check
      path. A blind retry with the same key would create a duplicate, which is exactly
      what the executor must never do.
    """

    is_authorized = True

    def __init__(self, simulate_timeout_for_product: str | None = None):
        self.simulate_timeout_for_product = simulate_timeout_for_product
        self._orders: dict[str, dict] = {}
        self._by_key: dict[str, dict] = {}
        self._pending_by_key: dict[str, str] = {}  # idempotency_key -> order_id (timeout case)
        self._timeout_done: set[str] = set()
        self._listings: dict[str, dict] = {}
        self._listings_by_key: dict[str, dict] = {}
        self._order_counter = 0
        self._listing_counter = 0
        # observability for tests
        self.place_order_calls = 0
        self.publish_listing_calls = 0

    def place_order(self, *, idempotency_key: str, product_id: str, quantity: int, buyer_id: str) -> dict:
        self.place_order_calls += 1
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]  # idempotent replay
        self._order_counter += 1
        result = {
            "order_id": f"MOCK-ORD-{self._order_counter:04d}",
            "status": "CONFIRMED",
            "product_id": product_id,
            "quantity": quantity,
            "buyer_id": buyer_id,
        }
        self._orders[result["order_id"]] = result
        if (
            self.simulate_timeout_for_product == product_id
            and idempotency_key not in self._timeout_done
        ):
            # The order exists server-side, but the caller never sees the response.
            self._timeout_done.add(idempotency_key)
            self._pending_by_key[idempotency_key] = result["order_id"]
            raise MarketplaceTimeoutError("simulated network timeout")
        self._by_key[idempotency_key] = result
        return result

    def get_order_status(self, identifier: str) -> dict | None:
        if identifier in self._orders:
            return self._orders[identifier]
        if identifier in self._by_key:
            return self._by_key[identifier]
        if identifier in self._pending_by_key:  # resolve by idempotency key after a timeout
            result = self._orders.get(self._pending_by_key[identifier])
            if result:
                self._by_key[identifier] = result
            return result
        return None

    def publish_listing(self, *, idempotency_key: str, listing: dict) -> dict:
        self.publish_listing_calls += 1
        if idempotency_key in self._listings_by_key:
            return self._listings_by_key[idempotency_key]
        self._listing_counter += 1
        result = {
            "listing_id": f"MOCK-LIST-{self._listing_counter:04d}",
            "status": "LIVE",
            "listing": listing,
        }
        self._listings[result["listing_id"]] = result
        self._listings_by_key[idempotency_key] = result
        return result

    def get_listing_status(self, listing_id: str) -> dict | None:
        return self._listings.get(listing_id)


class OfficialShopeeClient(ShopeeClient):
    """Official Shopee Open Platform (Open API v2) client — Phase 5.

    Real implementation against the official seller-side API surface:
    HMAC-SHA256 request signing (base string = partner_id + api_path + timestamp
    + access_token + shop_id, keyed with the partner key), common parameters
    (partner_id, timestamp, sign, sign_method=HMAC-SHA256, sign_version=V2.0,
    access_token, shop_id), and the endpoints a seller actually gets:

    - publish_listing   -> POST /api/v2/product/add_item
    - get_listing_status-> POST /api/v2/product/get_item_base_info
    - get_order_status  -> GET  /api/v2/order/get_order_detail  (seller-side read
                           of orders customers placed on the marketplace)

    Buyer-side automated ordering is NOT offered by official Shopee APIs, so
    place_order() always raises IntegrationUnavailableError — honestly. The buyer
    demo flow keeps using MockShopeeClient.

    Eligibility gate (Shopee Open Platform docs, checked in Phase 5): Shopee has no
    Pakistan marketplace; Open Platform eligibility covers SG/MY/TH/TW/ID/VN/PH/
    MX/CO/CL/CB-KR/CB-JP/BR/CB-CN with seller or ISV (partner) accounts meeting
    market-specific criteria. The client therefore stays unauthorized until real
    credentials (partner_id + partner_key + shop_id + access_token) are configured.

    The HTTP transport is injectable so the whole client is sandbox-testable
    without network access or real credentials. No scraping, no bypassing rules.
    """

    LIVE_BASE_URL = "https://partner.shopeemobile.com"
    SANDBOX_BASE_URL = "https://openplatform.sandbox.test-stable.shopee.sg"

    # Shopee order_status -> our Order.status vocabulary
    ORDER_STATUS_MAP = {
        "UNPAID": "PENDING",
        "READY_TO_SHIP": "CONFIRMED",
        "PROCESSED": "CONFIRMED",
        "SHIPPED": "SHIPPED",
        "COMPLETED": "DELIVERED",
        "CANCELLED": "CANCELLED",
        "IN_CANCEL": "CANCELLED",
        "TO_RETURN": "RETURNED",
        "RETURNED": "RETURNED",
    }
    # Shopee item_status -> listing status vocabulary
    LISTING_STATUS_MAP = {
        "NORMAL": "LIVE",
        "UNLIST": "UNLISTED",
        "BANNED": "BANNED",
        "REVIEWING": "REVIEWING",
        "SELLER_DELETE": "DELETED",
        "SHOPEE_DELETE": "DELETED",
    }

    def __init__(
        self,
        partner_id: str | None = None,
        partner_key: str | None = None,
        shop_id: str | None = None,
        access_token: str | None = None,
        base_url: str = LIVE_BASE_URL,
        category_id: str | None = None,
        transport=None,
    ):
        self.partner_id = partner_id
        self.partner_key = partner_key
        self.shop_id = shop_id
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.category_id = category_id
        self.is_authorized = bool(partner_id and partner_key and shop_id and access_token)
        # transport(method, url, params, json, timeout) -> response with .status_code/.json()
        # Injectable for sandbox tests; defaults to httpx.
        self._transport = transport
        self._published_by_key: dict[str, dict] = {}  # client-side replay cache

    # ------------------------------------------------------------------ #
    # Signing (official Shopee Open API v2 scheme)
    # ------------------------------------------------------------------ #
    def _sign(self, path: str, timestamp: int) -> str:
        base_string = f"{self.partner_id}{path}{timestamp}{self.access_token}{self.shop_id}"
        return hmac.new(
            (self.partner_key or "").encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _common_params(self, path: str) -> dict:
        timestamp = int(time.time())
        return {
            "partner_id": self.partner_id,
            "timestamp": timestamp,
            "sign": self._sign(path, timestamp),
            "sign_method": "HMAC-SHA256",
            "sign_version": "V2.0",
            "access_token": self.access_token,
            "shop_id": self.shop_id,
        }

    def _request(self, path: str, *, method: str = "GET", params: dict | None = None, body: dict | None = None) -> dict:
        if not self.is_authorized:
            raise IntegrationUnavailableError(
                "Shopee Open Platform credentials are not configured "
                "(SHOPEE_SHOPEE_PARTNER_ID / SHOPEE_SHOPEE_PARTNER_KEY / SHOPEE_SHOPEE_SHOP_ID / "
                "SHOPEE_SHOPEE_ACCESS_TOKEN). The action stays local — nothing was sent to Shopee."
            )
        url = f"{self.base_url}{path}"
        query = self._common_params(path)
        if params:
            query.update(params)
        try:
            if self._transport is not None:
                resp = self._transport(method, url, params=query, json=body, timeout=30)
            else:
                resp = httpx.request(method, url, params=query, json=body, timeout=30)
        except httpx.TimeoutException as e:
            # Outcome unknown — the executor records UNKNOWN and checks status, never retries.
            raise MarketplaceTimeoutError(f"Shopee API timed out: {e}") from e
        except httpx.HTTPError as e:
            raise MarketplaceError(f"Shopee API call failed: {e}") from e

        if resp.status_code >= 400:
            raise MarketplaceError(f"Shopee API HTTP {resp.status_code} on {path}")
        data = resp.json()
        # Shopee signals errors in-band: {"error": "error_x", "message": "...", "request_id": ...}
        if isinstance(data, dict) and data.get("error"):
            raise MarketplaceError(
                f"Shopee API error {data.get('error')} on {path}: {data.get('message')} "
                f"(request_id={data.get('request_id')})"
            )
        return data

    # ------------------------------------------------------------------ #
    # ShopeeClient interface
    # ------------------------------------------------------------------ #
    def place_order(self, *, idempotency_key: str, product_id: str, quantity: int, buyer_id: str) -> dict:
        # Official Shopee APIs have no buyer-side ordering endpoint — orders are placed
        # by customers on the marketplace. We report that honestly instead of faking it.
        raise IntegrationUnavailableError(
            "Official Shopee APIs do not support buyer-side automated ordering — orders are "
            "placed by customers on the marketplace itself. The buyer flow uses the mock "
            "client; seller-side order reads are available via get_order_status."
        )

    def get_order_status(self, identifier: str) -> dict | None:
        """Seller-side read: GET /api/v2/order/get_order_detail?order_sn_list=<sn>.

        `identifier` is a Shopee order_sn (or, after a timeout, our idempotency key —
        Shopee has no idempotency keys, so an unresolvable identifier returns None and
        the executor keeps the UNKNOWN state instead of blindly resubmitting).
        """
        data = self._request(
            "/api/v2/order/get_order_detail",
            method="GET",
            params={"order_sn_list": identifier, "response_optional_fields": "total_amount"},
        )
        order_list = (data.get("response") or {}).get("order_list") or []
        if not order_list:
            return None
        order = order_list[0]
        raw_status = order.get("order_status", "")
        return {
            "order_id": order.get("order_sn", identifier),
            "status": self.ORDER_STATUS_MAP.get(raw_status, raw_status or "UNKNOWN"),
            "raw_status": raw_status,
        }

    def publish_listing(self, *, idempotency_key: str, listing: dict) -> dict:
        """POST /api/v2/product/add_item — publish a listing to the seller's shop.

        Note: Shopee prices are in the shop's marketplace currency (TWD/THB/BRL/MXN...),
        not PKR — a currency conversion layer is a deployment-time concern (documented).
        The DB Execution table is the real idempotency guard; the in-memory cache below
        is a second, client-side safety net within one process.
        """
        if idempotency_key in self._published_by_key:
            return self._published_by_key[idempotency_key]
        item: dict = {
            "item_name": listing["title"],
            "description": listing.get("description") or "",
            "original_price": listing["price_pkr"],
            "normal_stock": int(listing["stock"]),
            "item_sku": f"SB-{idempotency_key[:16]}",
        }
        if self.category_id:
            item["category_id"] = int(self.category_id)
        data = self._request("/api/v2/product/add_item", method="POST", body={"item": item})
        item_id = (data.get("response") or {}).get("item_id") or data.get("item_id")
        if not item_id:
            raise MarketplaceError(f"Shopee add_item returned no item_id: {data}")
        result = {
            "listing_id": str(item_id),
            "status": "LIVE",
            "listing": listing,
        }
        self._published_by_key[idempotency_key] = result
        return result

    def get_listing_status(self, listing_id: str) -> dict | None:
        """POST /api/v2/product/get_item_base_info — read a listing's live status."""
        try:
            item_id = int(listing_id)
        except (TypeError, ValueError):
            return None
        data = self._request(
            "/api/v2/product/get_item_base_info", method="POST", body={"item_id": item_id}
        )
        item_list = (data.get("response") or {}).get("item_list") or []
        if not item_list:
            return None
        item = item_list[0]
        raw_status = item.get("item_status", "")
        return {
            "listing_id": str(item.get("item_id", listing_id)),
            "status": self.LISTING_STATUS_MAP.get(raw_status, raw_status or "UNKNOWN"),
            "raw_status": raw_status,
            "title": item.get("item_name"),
        }
