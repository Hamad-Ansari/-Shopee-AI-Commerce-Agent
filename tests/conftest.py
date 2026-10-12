import os
import tempfile

# Must be set before any backend module is imported (engine is created at import time).
os.environ["SHOPEE_DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='shopbot-test-')}/test.db"
# Deterministic intent extraction for tests; LLM provider behaviour is tested separately.
os.environ["SHOPEE_LLM_PROVIDER"] = "rulebased"
# Isolated persistent checkpointer file for the whole test run (deleted per test below).
CHECKPOINTER_PATH = f"{tempfile.mkdtemp(prefix='shopbot-ckpt-')}/checkpoints.db"
os.environ["SHOPEE_CHECKPOINTER_PATH"] = CHECKPOINTER_PATH

import pytest  # noqa: E402

from backend import dependencies  # noqa: E402
from backend.database import models  # noqa: E402,F401  (registers tables)
from backend.database.session import Base, SessionLocal, engine  # noqa: E402
from backend.integrations.shopee_client import MockShopeeClient  # noqa: E402
from backend.integrations.whatsapp_client import MockWhatsAppClient  # noqa: E402


class FakeOllamaClient:
    """Injectable stand-in for ollama.Client: returns canned chat responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}}


@pytest.fixture(autouse=True)
def fresh_env():
    """Fresh DB tables, fresh mock marketplace, fresh rate limiter, reloaded catalog."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    client = MockShopeeClient()
    dependencies.set_shopee_client(client)
    dependencies.reset_policy_engine()
    dependencies.set_llm_provider(None)  # rebuild from settings (rulebased in tests)
    dependencies.set_whatsapp_client(MockWhatsAppClient())
    dependencies.get_product_service().reload()
    # Phase 6 isolation: pristine orchestrator (fresh checkpointer file), rate
    # limiter and metrics so no state leaks between tests.
    dependencies.set_orchestrator(None)
    if os.path.exists(CHECKPOINTER_PATH):
        os.remove(CHECKPOINTER_PATH)
    from backend.middleware import reset_rate_limiter
    from backend.monitoring import reset_metrics

    reset_rate_limiter()
    reset_metrics()
    yield client


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def orch():
    return dependencies.get_orchestrator()


@pytest.fixture()
def registered_user(db, fresh_env):
    """A TestClient plus a registered user's credentials and auth headers."""
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as c:
        r = c.post(
            "/auth/register",
            json={"user_id": "test-user", "password": "secret123", "role": "buyer"},
        )
        assert r.status_code == 201
        key = r.json()["api_key"]
        yield c, "test-user", key, {"Authorization": f"Bearer {key}"}
