"""Phase 6 — security tests: auth, secrets handling, rate limiting, headers,
CORS, monitoring endpoints, and hardened deployment artifacts."""

import os

import pytest
from fastapi.testclient import TestClient

from backend import dependencies
from backend.database import models
from backend.middleware import get_rate_limiter
from backend.main import app
from backend.monitoring import get_metrics

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# Authentication & authorization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/chat"),
        ("GET", "/orders"),
        ("GET", "/audit"),
        ("GET", "/proposals/whatever"),
        ("POST", "/proposals/whatever/approve"),
        ("POST", "/proposals/whatever/reject"),
        ("POST", "/proposals/whatever/check-status"),
        ("GET", "/auth/me"),
    ],
)
def test_protected_endpoints_require_auth(db, fresh_env, method, path):
    with TestClient(app) as c:
        r = c.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 401, f"{method} {path} must require auth, got {r.status_code}"


def test_cross_user_access_is_forbidden(db, fresh_env):
    with TestClient(app) as c:
        # two users
        r = c.post("/auth/register", json={"user_id": "sec-a", "password": "secret123", "role": "buyer"})
        headers_a = {"Authorization": f"Bearer {r.json()['api_key']}"}
        r = c.post("/auth/register", json={"user_id": "sec-b", "password": "secret123", "role": "buyer"})
        headers_b = {"Authorization": f"Bearer {r.json()['api_key']}"}

        # user A chats and buys -> proposal owned by A
        c.post("/chat", json={"user_id": "sec-a", "message": "find earbuds under PKR 2,000"}, headers=headers_a)
        r = c.post("/chat", json={"user_id": "sec-a", "message": "buy the first one"}, headers=headers_a)
        pid = r.json()["proposal"]["id"]

        # user B cannot see or approve A's proposal
        r = c.get(f"/proposals/{pid}", headers=headers_b)
        assert r.status_code in (403, 404)
        r = c.post(f"/proposals/{pid}/approve", headers=headers_b)
        assert r.status_code in (403, 404)
        r = c.get("/audit", headers=headers_b)
        assert r.status_code == 200
        assert all(e["user_id"] == "sec-b" for e in r.json()["events"])

        # chat with a mismatched user_id is rejected
        r = c.post("/chat", json={"user_id": "sec-b", "message": "hi"}, headers=headers_a)
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Secrets handling
# --------------------------------------------------------------------------- #
def test_me_never_exposes_secrets(db, fresh_env):
    with TestClient(app) as c:
        r = c.post("/auth/register", json={"user_id": "sec-me", "password": "secret123", "role": "buyer"})
        assert r.status_code == 201
        key = r.json()["api_key"]
        assert key.startswith("sb_")
        # the raw key works exactly once as a bearer token
        r = c.get("/auth/me", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        body = r.text.lower()
        for secret_field in ("api_key_hash", "password_hash", "password", "secret123", key.lower()):
            assert secret_field not in body, f"response leaks {secret_field}"


def test_credentials_stored_hashed_not_plaintext(db, fresh_env):
    with TestClient(app) as c:
        r = c.post("/auth/register", json={"user_id": "sec-hash", "password": "secret123", "role": "buyer"})
        key = r.json()["api_key"]
    user = db.get(models.User, "sec-hash")
    assert user.password_hash != "secret123"
    assert user.password_hash.startswith("pbkdf2")
    assert user.api_key_hash != key  # only the sha256 of the key is stored
    assert len(user.api_key_hash) == 64


# --------------------------------------------------------------------------- #
# Security headers & CORS
# --------------------------------------------------------------------------- #
def test_security_headers_present(db, fresh_env):
    with TestClient(app) as c:
        r = c.get("/")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_cors_is_allowlist_not_wildcard(db, fresh_env):
    with TestClient(app) as c:
        r = c.get("/", headers={"Origin": "http://localhost:8501"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:8501"
        # a foreign origin gets no CORS grant
        r = c.get("/", headers={"Origin": "https://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") != "*"
        assert "evil.example.com" not in (r.headers.get("access-control-allow-origin") or "")


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_rate_limit_returns_429_with_retry_after(db, fresh_env, monkeypatch):
    monkeypatch.setattr(get_rate_limiter(), "limit", 5)
    with TestClient(app) as c:
        codes = [c.get("/products/search?q=earbuds").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5
        assert codes[5] == 429
        assert codes[6] == 429
        r = c.get("/products/search?q=earbuds")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert get_metrics().rate_limited_total >= 2


def test_rate_limit_is_per_principal(db, fresh_env, monkeypatch):
    monkeypatch.setattr(get_rate_limiter(), "limit", 3)
    with TestClient(app) as c:
        r = c.post("/auth/register", json={"user_id": "sec-rl-a", "password": "secret123", "role": "buyer"})
        headers_a = {"Authorization": f"Bearer {r.json()['api_key']}"}
        r = c.post("/auth/register", json={"user_id": "sec-rl-b", "password": "secret123", "role": "buyer"})
        headers_b = {"Authorization": f"Bearer {r.json()['api_key']}"}

        for _ in range(3):
            assert c.get("/auth/me", headers=headers_a).status_code == 200
        assert c.get("/auth/me", headers=headers_a).status_code == 429  # A is limited
        assert c.get("/auth/me", headers=headers_b).status_code == 200  # B is not


def test_health_and_metrics_are_rate_limit_exempt(db, fresh_env, monkeypatch):
    monkeypatch.setattr(get_rate_limiter(), "limit", 2)
    with TestClient(app) as c:
        for _ in range(10):
            assert c.get("/health/live").status_code == 200
            assert c.get("/health/ready").status_code == 200
            assert c.get("/metrics").status_code == 200
        assert c.get("/products/search?q=x").status_code == 200  # 1st non-exempt hit
        assert c.get("/products/search?q=x").status_code == 200  # 2nd
        assert c.get("/products/search?q=x").status_code == 429  # 3rd -> limited


# --------------------------------------------------------------------------- #
# Monitoring endpoints
# --------------------------------------------------------------------------- #
def test_metrics_snapshot_shape(db, fresh_env):
    with TestClient(app) as c:
        c.get("/")
        c.get("/products/search?q=earbuds")
        r = c.get("/metrics")
        assert r.status_code == 200
        m = r.json()
        # /metrics counts itself only after the snapshot is built: / + search = 2
        assert m["http_requests_total"] >= 2
        assert "200" in m["http_requests_by_status"]
        assert "database" in m
        assert m["database"]["users_total"] == 0  # fresh test DB
        assert "audit_events_by_type" in m["database"]


def test_health_ready_checks_the_database(db, fresh_env):
    with TestClient(app) as c:
        assert c.get("/health/ready").json()["status"] == "ready"
        assert c.get("/health/live").json()["status"] == "alive"


# --------------------------------------------------------------------------- #
# Hardened deployment artifacts
# --------------------------------------------------------------------------- #
def test_dockerfile_is_hardened():
    with open(os.path.join(PROJECT_ROOT, "Dockerfile")) as f:
        dockerfile = f.read()
    user_lines = [ln.strip() for ln in dockerfile.splitlines() if ln.strip().startswith("USER ")]
    assert user_lines == ["USER appuser"]  # exactly one USER directive, non-root
    assert "HEALTHCHECK" in dockerfile  # container health probe


def test_dev_compose_is_hardened():
    import yaml

    with open(os.path.join(PROJECT_ROOT, "docker-compose.yml")) as f:
        compose = yaml.safe_load(f)
    services = compose["services"]
    for name in ("db", "backend", "dashboard"):
        assert services[name].get("restart") == "unless-stopped", f"{name} missing restart policy"
    assert "healthcheck" in services["db"]  # db health gate
    assert services["backend"]["depends_on"]["db"]["condition"] == "service_healthy"


def test_prod_compose_has_tls_proxy_and_backups():
    import yaml

    with open(os.path.join(PROJECT_ROOT, "docker-compose.prod.yml")) as f:
        prod = yaml.safe_load(f)
    services = prod["services"]
    # Caddy reverse proxy with HTTPS (auto-TLS) on 80/443
    proxy = services["proxy"]
    assert "caddy" in proxy["image"]
    assert "443:443" in proxy["ports"]
    # backend/dashboard publish no ports directly in prod (proxy-only)
    assert not services["backend"].get("ports"), "backend must not publish ports in prod"
    assert not services["dashboard"].get("ports"), "dashboard must not publish ports in prod"
    # backup sidecar + volumes
    assert "backup" in services
    assert "pg_dump" in services["backup"]["command"]
    assert "backups" in prod["volumes"]
    # secrets come from the environment, never hardcoded
    assert "${POSTGRES_PASSWORD" in str(services["db"]["environment"])


def test_backup_script_exists_and_is_executable():
    script = os.path.join(PROJECT_ROOT, "scripts", "backup_db.sh")
    assert os.path.exists(script)
    assert os.access(script, os.X_OK)
    with open(script) as f:
        content = f.read()
    assert "pg_dump" in content  # postgres backups
    assert "sqlite" in content.lower()  # sqlite backups
