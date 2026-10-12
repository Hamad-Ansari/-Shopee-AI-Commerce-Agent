"""Phase 5: OfficialShopeeClient — official Shopee Open Platform v2 client.

Sandbox-tested end-to-end with an injected fake transport (no network, no real
credentials). Covers the official HMAC-SHA256 signing scheme, the seller-side
endpoints (add_item / get_item_base_info / get_order_detail), idempotent replay,
and the failure cases (unauthorized, API error, HTTP error, timeout, buyer-side
ordering not offered).
"""

import types

import httpx
import pytest

from backend.exceptions import IntegrationUnavailableError, MarketplaceError, MarketplaceTimeoutError
from backend.integrations import shopee_client as sc
from backend.integrations.shopee_client import OfficialShopeeClient

CREDS = dict(
    partner_id="2000001",
    partner_key="test-partner-key",
    shop_id="123456",
    access_token="test-access-token",
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeTransport:
    """Records calls and replays queued responses (or raises a queued exception)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params or {}, "json": json})
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def make_client(transport, **overrides):
    kwargs = dict(CREDS)
    kwargs.update(overrides)
    return OfficialShopeeClient(transport=transport, **kwargs)


LISTING = {"title": "Wireless Earbuds Pro", "category": "Electronics", "price_pkr": 1500.0, "stock": 10, "description": "Noise cancelling"}


# --------------------------------------------------------------------------- #
# Authorization gate
# --------------------------------------------------------------------------- #
def test_unauthorized_until_credentials():
    client = OfficialShopeeClient()
    assert not client.is_authorized
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {}}))
    client._transport = transport
    with pytest.raises(IntegrationUnavailableError):
        client.publish_listing(idempotency_key="k1", listing=LISTING)
    with pytest.raises(IntegrationUnavailableError):
        client.get_listing_status("123")
    with pytest.raises(IntegrationUnavailableError):
        client.get_order_status("SN1")
    assert transport.calls == []  # nothing was sent


def test_partial_credentials_still_unauthorized():
    client = OfficialShopeeClient(partner_id="1", partner_key="k", shop_id="s")  # no access token
    assert not client.is_authorized


def test_place_order_not_offered_even_when_authorized():
    # Official Shopee APIs have no buyer-side ordering endpoint — reported honestly.
    client = make_client(FakeTransport())
    assert client.is_authorized
    with pytest.raises(IntegrationUnavailableError):
        client.place_order(idempotency_key="k1", product_id="P1", quantity=1, buyer_id="u1")


# --------------------------------------------------------------------------- #
# Signing (official V2 scheme)
# --------------------------------------------------------------------------- #
def test_signature_known_answer():
    client = make_client(FakeTransport())
    # base string = partner_id + path + timestamp + access_token + shop_id
    sign = client._sign("/api/v2/product/add_item", 1700000000)
    assert sign == "de60d8cd82834e1d196a7ddb55d9f4a4cf2e3062e82901564d20c70c335deb2c"


def test_common_params_shape(monkeypatch):
    monkeypatch.setattr(sc, "time", types.SimpleNamespace(time=lambda: 1700000000))
    client = make_client(FakeTransport())
    params = client._common_params("/api/v2/product/add_item")
    assert params["partner_id"] == "2000001"
    assert params["timestamp"] == 1700000000
    assert params["sign"] == "de60d8cd82834e1d196a7ddb55d9f4a4cf2e3062e82901564d20c70c335deb2c"
    assert params["sign_method"] == "HMAC-SHA256"
    assert params["sign_version"] == "V2.0"
    assert params["access_token"] == "test-access-token"
    assert params["shop_id"] == "123456"


# --------------------------------------------------------------------------- #
# publish_listing -> POST /api/v2/product/add_item
# --------------------------------------------------------------------------- #
def test_publish_listing_happy_path():
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {"item_id": 987654}}))
    client = make_client(transport)
    result = client.publish_listing(idempotency_key="key-abc", listing=LISTING)
    assert result["listing_id"] == "987654"
    assert result["status"] == "LIVE"

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v2/product/add_item")
    item = call["json"]["item"]
    assert item["item_name"] == "Wireless Earbuds Pro"
    assert item["original_price"] == 1500.0
    assert item["normal_stock"] == 10
    assert item["description"] == "Noise cancelling"
    assert "category_id" not in item  # not configured
    # signed shop-API call: token + shop id in the query
    assert call["params"]["access_token"] == "test-access-token"
    assert call["params"]["shop_id"] == "123456"
    assert call["params"]["sign"]


def test_publish_listing_includes_category_when_configured():
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {"item_id": 1}}))
    client = make_client(transport, category_id="100123")
    client.publish_listing(idempotency_key="k", listing=LISTING)
    assert transport.calls[0]["json"]["item"]["category_id"] == 100123


def test_publish_listing_idempotent_replay():
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {"item_id": 42}}))
    client = make_client(transport)
    first = client.publish_listing(idempotency_key="dup-key", listing=LISTING)
    second = client.publish_listing(idempotency_key="dup-key", listing=LISTING)
    assert first == second
    assert len(transport.calls) == 1  # the marketplace was called exactly once


def test_publish_listing_api_error():
    transport = FakeTransport(
        FakeResponse(payload={"error": "error_price", "message": "invalid price", "request_id": "r1"})
    )
    client = make_client(transport)
    with pytest.raises(MarketplaceError, match="error_price"):
        client.publish_listing(idempotency_key="k", listing=LISTING)


def test_publish_listing_http_error():
    transport = FakeTransport(FakeResponse(status_code=500, payload={}))
    client = make_client(transport)
    with pytest.raises(MarketplaceError, match="HTTP 500"):
        client.publish_listing(idempotency_key="k", listing=LISTING)


def test_timeout_maps_to_marketplace_timeout():
    transport = FakeTransport(httpx.TimeoutException("timed out"))
    client = make_client(transport)
    with pytest.raises(MarketplaceTimeoutError):
        client.publish_listing(idempotency_key="k", listing=LISTING)


def test_add_item_without_item_id_is_an_error():
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {}}))
    client = make_client(transport)
    with pytest.raises(MarketplaceError, match="no item_id"):
        client.publish_listing(idempotency_key="k", listing=LISTING)


# --------------------------------------------------------------------------- #
# get_listing_status -> POST /api/v2/product/get_item_base_info
# --------------------------------------------------------------------------- #
def test_get_listing_status_live():
    transport = FakeTransport(
        FakeResponse(payload={"error": "", "response": {"item_list": [{"item_id": 5, "item_status": "NORMAL", "item_name": "Earbuds"}]}})
    )
    client = make_client(transport)
    result = client.get_listing_status("5")
    assert result == {"listing_id": "5", "status": "LIVE", "raw_status": "NORMAL", "title": "Earbuds"}
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v2/product/get_item_base_info")
    assert call["json"] == {"item_id": 5}


@pytest.mark.parametrize("raw,expected", [("UNLIST", "UNLISTED"), ("BANNED", "BANNED"), ("REVIEWING", "REVIEWING"), ("SELLER_DELETE", "DELETED")])
def test_get_listing_status_mapping(raw, expected):
    transport = FakeTransport(
        FakeResponse(payload={"error": "", "response": {"item_list": [{"item_id": 7, "item_status": raw}]}})
    )
    client = make_client(transport)
    assert client.get_listing_status("7")["status"] == expected


def test_get_listing_status_not_found():
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {"item_list": []}}))
    client = make_client(transport)
    assert client.get_listing_status("999") is None


def test_get_listing_status_non_numeric_id():
    client = make_client(FakeTransport())
    assert client.get_listing_status("not-a-number") is None


# --------------------------------------------------------------------------- #
# get_order_status -> GET /api/v2/order/get_order_detail (seller-side read)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("UNPAID", "PENDING"),
        ("READY_TO_SHIP", "CONFIRMED"),
        ("PROCESSED", "CONFIRMED"),
        ("SHIPPED", "SHIPPED"),
        ("COMPLETED", "DELIVERED"),
        ("CANCELLED", "CANCELLED"),
        ("IN_CANCEL", "CANCELLED"),
        ("SOMETHING_NEW", "SOMETHING_NEW"),  # unknown statuses pass through, uppercased
    ],
)
def test_get_order_status_mapping(raw, expected):
    transport = FakeTransport(
        FakeResponse(payload={"error": "", "response": {"order_list": [{"order_sn": "SN123", "order_status": raw}]}})
    )
    client = make_client(transport)
    result = client.get_order_status("SN123")
    assert result == {"order_id": "SN123", "status": expected, "raw_status": raw}
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/api/v2/order/get_order_detail")
    assert call["params"]["order_sn_list"] == "SN123"


def test_get_order_status_not_found():
    transport = FakeTransport(FakeResponse(payload={"error": "", "response": {"order_list": []}}))
    client = make_client(transport)
    assert client.get_order_status("SN-UNKNOWN") is None


def test_get_order_status_api_error():
    transport = FakeTransport(FakeResponse(payload={"error": "error_auth", "message": "Invalid access_token."}))
    client = make_client(transport)
    with pytest.raises(MarketplaceError, match="error_auth"):
        client.get_order_status("SN123")
