"""Phase 6 — HTTP middleware: rate limiting + observability/security headers.

- RateLimitMiddleware: per-principal sliding window (API key when present, else
  client IP) using SHOPEE_RATE_LIMIT_PER_MINUTE. Health/metrics/docs/webhook-verify
  paths are exempt. Exceeding the limit -> 429 JSON + Retry-After, audited metric.
  In-memory per process (multi-worker deployments should front this with a shared
  limiter — e.g. Redis — or load-balancer rate limiting; documented limitation).
- ObservabilityMiddleware: times every request, records metrics, adds security
  headers and writes a one-line access log.
"""

import logging
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import get_settings
from backend.monitoring import get_metrics

logger = logging.getLogger("shopbot.access")

# Paths that must never be rate limited (health probes, docs, Meta verification).
RATE_LIMIT_EXEMPT_PATHS = {
    "/",
    "/health/live",
    "/health/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-XSS-Protection": "0",  # modern CSP replaces the legacy auditor
}


class RateLimiter:
    """Sliding-window rate limiter, one bucket per principal."""

    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, principal: str) -> int:
        """Record a hit; return seconds to wait (0 = allowed)."""
        now = time.monotonic()
        window = self._hits[principal]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self.limit:
            return int(60 - (now - window[0])) + 1
        window.append(now)
        return 0

    def reset(self) -> None:
        self._hits.clear()


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().rate_limit_per_minute)
    return _limiter


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in RATE_LIMIT_EXEMPT_PATHS or (path == "/webhooks/whatsapp" and request.method == "GET"):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        principal = auth.split(" ", 1)[1].strip() if auth.startswith("Bearer ") else (request.client.host if request.client else "unknown")

        limiter = get_rate_limiter()
        retry_after = limiter.check(principal)
        if retry_after > 0:
            get_metrics().record_rate_limited()
            logger.warning("rate limited %s %s %s", request.method, path, principal[:12])
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded — please slow down and try again shortly."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        get_metrics().record_request(response.status_code, duration)
        logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            duration * 1000,
        )
        return response
