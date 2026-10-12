from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.session import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="buyer")  # buyer | seller
    spending_limit_pkr: Mapped[float] = mapped_column(Float, default=50000.0)
    password_hash: Mapped[str] = mapped_column(String, default="")  # pbkdf2_sha256$...
    api_key_hash: Mapped[str] = mapped_column(String, default="")  # sha256 hex; plaintext shown once
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Session(Base):
    """Per-user conversation state (last search results, pending proposal)."""

    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Proposal(Base):
    """A structured action awaiting (or having received) approval.

    Status machine: REQUESTED -> VALIDATED -> PENDING_APPROVAL -> (REJECTED | APPROVED)
    -> REVALIDATED -> EXECUTING -> (SUCCEEDED | FAILED | UNKNOWN)
    """

    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    action_type: Mapped[str] = mapped_column(String)  # place_order | publish_listing
    status: Mapped[str] = mapped_column(String, default="REQUESTED")
    items: Mapped[list] = mapped_column(JSON, default=list)
    shipping_fee_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    total_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    payload_hash: Mapped[str] = mapped_column(String, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    result_ref: Mapped[str] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Approval(Base):
    """An approval bound to proposal_id + proposal_version + user_id + payload_hash."""

    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("proposal_id", "user_id", name="uq_approval_proposal_user"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    proposal_version: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String, index=True)
    payload_hash: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    proposal_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    total_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="CONFIRMED")
    marketplace_order_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    seller_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="General")
    price_pkr: Mapped[float] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="DRAFT")  # DRAFT | PUBLISHED | REJECTED
    marketplace_listing_id: Mapped[str] = mapped_column(String, nullable=True)
    proposal_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Execution(Base):
    """Persistent idempotency store: one row per idempotency key, forever.

    The executor checks this table before calling the marketplace, so an
    idempotency key can never produce a second execution — even across restarts.
    Only SUCCEEDED executions are recorded; a FAILED execution leaves no row, so a
    fresh proposal (with a fresh key) can retry.
    """

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    action_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="SUCCEEDED")
    result_ref: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    proposal_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WaMessage(Base):
    """Processed WhatsApp message ids — dedup for Meta webhook retries.

    Meta re-delivers the same payload when the endpoint is slow or errors; the
    wamid is unique per message, so one row per id gives at-most-once processing.
    The executor's idempotency store remains the real guard for consequential
    actions (a double button tap can never create two orders).
    """

    __tablename__ = "wa_messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    wa_type: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
