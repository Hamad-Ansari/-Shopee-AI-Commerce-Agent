from fastapi.testclient import TestClient

from backend.main import app


def test_health(db, fresh_env):
    with TestClient(app) as c:
        assert c.get("/").json()["status"] == "ok"


def test_chat_buy_approve_flow(registered_user, fresh_env):
    c, user_id, key, headers = registered_user

    r = c.post("/chat", json={"user_id": user_id, "message": "Find wireless earbuds under PKR 2,000"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert 1 <= len(data["products"]) <= 3
    assert all(p["price_pkr"] <= 2000 for p in data["products"])

    r = c.post("/chat", json={"user_id": user_id, "message": "buy the first one"}, headers=headers)
    data = r.json()
    assert data["needs_approval"] is True
    pid = data["proposal"]["id"]

    r1 = c.post(f"/proposals/{pid}/approve", headers=headers).json()
    assert r1["ok"] and r1["order"]
    r2 = c.post(f"/proposals/{pid}/approve", headers=headers).json()
    assert r2["order"]["id"] == r1["order"]["id"]  # idempotent

    orders = c.get("/orders", headers=headers).json()["orders"]
    assert len(orders) == 1
    assert fresh_env.place_order_calls == 1


def test_products_search_is_public(db, fresh_env):
    with TestClient(app) as c:
        data = c.get("/products/search", params={"q": "earbuds", "max_price_pkr": 2000, "limit": 10}).json()
        assert data["products"]
        assert all(p["price_pkr"] <= 2000 for p in data["products"])
        assert c.get("/products/PROD-EAR-001").status_code == 200
        assert c.get("/products/NOPE").status_code == 404


def test_webhook_accepts_meta_payloads(db, fresh_env):
    with TestClient(app) as c:
        # empty/heartbeat payload -> 200, nothing to answer
        r = c.post("/webhooks/whatsapp", json={"object": "whatsapp_business_account", "entry": []})
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "replies": 0}
        # invalid JSON -> 400
        r = c.post("/webhooks/whatsapp", content=b"not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 400
