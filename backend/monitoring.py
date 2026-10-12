"""Phase 6 — lightweight monitoring: in-memory request metrics + DB aggregates.

No external dependencies (no Prometheus client): /metrics returns a JSON snapshot
suitable for scraping, dashboards or a simple uptime probe. Counters are per-process
— for multi-worker deployments, aggregate at the load balancer / scrape all workers.
"""

import threading
import time
from datetime import datetime, timezone


class MetricsRegistry:
    """Thread-safe in-memory counters for the current process."""

    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.http_requests_total = 0
        self.http_requests_by_status: dict[int, int] = {}
        self.http_request_duration_seconds_sum = 0.0
        self.rate_limited_total = 0
        self.errors_total = 0

    def record_request(self, status_code: int, duration_seconds: float) -> None:
        with self._lock:
            self.http_requests_total += 1
            self.http_requests_by_status[status_code] = self.http_requests_by_status.get(status_code, 0) + 1
            self.http_request_duration_seconds_sum += duration_seconds
            if status_code >= 500:
                self.errors_total += 1

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited_total += 1

    def reset(self) -> None:
        with self._lock:
            self.__init__()

    def snapshot(self, db=None) -> dict:
        with self._lock:
            avg = (
                self.http_request_duration_seconds_sum / self.http_requests_total
                if self.http_requests_total
                else 0.0
            )
            snap = {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "http_requests_total": self.http_requests_total,
                "http_requests_by_status": {str(k): v for k, v in sorted(self.http_requests_by_status.items())},
                "http_request_duration_avg_seconds": round(avg, 4),
                "rate_limited_total": self.rate_limited_total,
                "errors_5xx_total": self.errors_total,
            }
        if db is not None:
            snap["database"] = _db_aggregates(db)
        return snap


def _db_aggregates(db) -> dict:
    """Aggregate business counters straight from the source of truth (the DB)."""
    from sqlalchemy import func

    from backend.database import models

    def count_by(model, column):
        rows = db.query(column, func.count()).group_by(column).all()
        return {str(k): v for k, v in rows}

    audit_rows = (
        db.query(models.AuditEvent.event_type, func.count()).group_by(models.AuditEvent.event_type).all()
    )
    return {
        "users_total": db.query(func.count()).select_from(models.User).scalar() or 0,
        "proposals_by_status": count_by(models.Proposal, models.Proposal.status),
        "orders_by_status": count_by(models.Order, models.Order.status),
        "listings_by_status": count_by(models.Listing, models.Listing.status),
        "audit_events_total": db.query(func.count()).select_from(models.AuditEvent).scalar() or 0,
        "audit_events_by_type": {k: v for k, v in audit_rows},
        "executions_total": db.query(func.count()).select_from(models.Execution).scalar() or 0,
    }


_registry = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _registry


def reset_metrics() -> None:
    _registry.reset()
