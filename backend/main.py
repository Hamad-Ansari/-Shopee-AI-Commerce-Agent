from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.api import audit, auth, chat, orders, products, webhooks
from backend.config import get_settings
from backend.database.session import SessionLocal, init_db
from backend.middleware import ObservabilityMiddleware, RateLimitMiddleware
from backend.monitoring import get_metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Shopee AI Commerce Agent — Phase 6 (deployment-ready)", version="0.6.0", lifespan=lifespan)

# Phase 6: CORS is allow-list based (dev defaults: the local Streamlit dashboard).
# Tighten SHOPEE_CORS_ALLOW_ORIGINS to your real dashboard origin in production.
_origins = [o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
# Rate limiting first (outermost), then observability (headers + timing + metrics).
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(audit.router)
app.include_router(webhooks.router)


@app.get("/")
def health():
    return {"status": "ok", "phase": 6, "app": "shopee-ai-commerce-agent"}


@app.get("/health/live")
def health_live():
    """Liveness probe — the process is up (no dependencies checked)."""
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    """Readiness probe — the database answers."""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        return {"status": "not_ready", "error": str(e)}, 503
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    """JSON metrics snapshot: process counters + DB aggregates (scrape-friendly)."""
    db = SessionLocal()
    try:
        return get_metrics().snapshot(db)
    finally:
        db.close()
