"""Phase 2: authentication and authorization tests."""

from fastapi.testclient import TestClient

from backend.main import app
from backend.services import auth_service


def test_register_and_me(registered_user):
    c, user_id, key, headers = registered_user
    r = c.get("/auth/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == user_id
    # Secrets must never leak through the API.
    assert "api_key" not in body
    assert "password_hash" not in body
    assert "api_key_hash" not in body


def test_register_duplicate_conflict(registered_user):
    c, user_id, *_ = registered_user
    r = c.post("/auth/register", json={"user_id": user_id, "password": "secret123"})
    assert r.status_code == 409


def test_register_short_password_rejected(db, fresh_env):
    with TestClient(app) as c:
        r = c.post("/auth/register", json={"user_id": "x", "password": "123"})
        assert r.status_code == 422


def test_missing_or_invalid_key_is_401(registered_user):
    c, *_ = registered_user
    assert c.get("/auth/me").status_code == 401
    assert c.get("/auth/me", headers={"Authorization": "Bearer wrong-key"}).status_code == 401
    assert c.get("/auth/me", headers={"Authorization": "Token abc"}).status_code == 401
    assert c.post("/chat", json={"message": "hi"}).status_code == 401
    assert c.get("/orders").status_code == 401
    assert c.get("/audit").status_code == 401


def test_chat_user_id_must_match_authenticated_user(registered_user):
    c, user_id, key, headers = registered_user
    r = c.post("/chat", json={"user_id": "someone-else", "message": "hi"}, headers=headers)
    assert r.status_code == 403
    # Without a user_id the chat acts as the authenticated user.
    r = c.post("/chat", json={"message": "hi"}, headers=headers)
    assert r.status_code == 200


def test_proposal_and_order_ownership_enforced(registered_user):
    c, user_id, key, headers = registered_user
    c.post("/chat", json={"message": "find earbuds"}, headers=headers)
    r = c.post("/chat", json={"message": "buy the first one"}, headers=headers)
    pid = r.json()["proposal"]["id"]

    r = c.post("/auth/register", json={"user_id": "user-b", "password": "secret123"})
    h_b = {"Authorization": f"Bearer {r.json()['api_key']}"}
    assert c.post(f"/proposals/{pid}/approve", headers=h_b).status_code == 403
    assert c.post(f"/proposals/{pid}/reject", headers=h_b).status_code == 403
    assert c.get(f"/proposals/{pid}", headers=h_b).status_code == 403
    assert c.get("/orders/some-order-id", headers=h_b).status_code == 404


def test_audit_endpoint_returns_only_own_events(registered_user):
    c, user_id, key, headers = registered_user
    c.post("/chat", json={"message": "find earbuds"}, headers=headers)
    r = c.get("/audit", headers=headers)
    assert r.status_code == 200
    events = r.json()["events"]
    assert events
    assert all(e["user_id"] == user_id for e in events)
    assert any(e["event_type"] == "search" for e in events)

    r2 = c.get("/audit", params={"event_type": "intent"}, headers=headers)
    assert r2.json()["events"]
    assert all(e["event_type"] == "intent" for e in r2.json()["events"])


def test_password_hashing_unit():
    stored = auth_service.hash_password("hunter2")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth_service.verify_password("hunter2", stored)
    assert not auth_service.verify_password("hunter3", stored)
    assert not auth_service.verify_password("hunter2", "garbage")
    assert not auth_service.verify_password("hunter2", "")


def test_api_key_hashing_unit():
    key = auth_service.generate_api_key()
    assert key.startswith("sb_")
    assert auth_service.hash_api_key(key) == auth_service.hash_api_key(key)
    assert auth_service.hash_api_key(key) != auth_service.hash_api_key(key + "x")
