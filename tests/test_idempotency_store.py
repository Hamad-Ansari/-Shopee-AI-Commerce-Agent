"""Phase 2: the persistent idempotency store (executions table)."""

import pytest
from sqlalchemy.exc import IntegrityError

from backend import dependencies
from backend.database import models
from backend.exceptions import MarketplaceError
from backend.integrations.shopee_client import MockShopeeClient
from backend.services import order_service


def chat(orch, db, user_id, message, role=""):
    return orch.handle_message(db, user_id, message, role=role)


def test_execution_recorded_and_replayed_after_state_loss(orch, db, fresh_env):
    chat(orch, db, "idem-1", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "idem-1", "buy the first one")
    pid = r.proposal.id
    key = order_service.get_proposal_row(db, pid).idempotency_key

    o1 = order_service.approve_and_execute(db, pid, "idem-1")
    assert o1.ok and o1.order

    exec_row = db.query(models.Execution).filter_by(idempotency_key=key).first()
    assert exec_row is not None
    assert exec_row.result_ref == o1.order.marketplace_order_id
    assert exec_row.action_type == "place_order"

    # Simulate lost proposal state (crash/restore): the idempotency store must still
    # prevent a second marketplace call.
    proposal = order_service.get_proposal_row(db, pid)
    proposal.status = "PENDING_APPROVAL"
    db.commit()

    o2 = order_service.approve_and_execute(db, pid, "idem-1")
    assert o2.ok
    assert "already executed" in o2.message.lower()
    assert db.query(models.Order).count() == 1
    assert fresh_env.place_order_calls == 1
    events = {e.event_type for e in db.query(models.AuditEvent).filter_by(user_id="idem-1").all()}
    assert "execution_replayed" in events


def test_execution_idempotency_key_is_unique(db):
    db.add(models.Execution(id="e1", idempotency_key="k1", proposal_id="p", user_id="u",
                            action_type="place_order", status="SUCCEEDED"))
    db.commit()
    db.add(models.Execution(id="e2", idempotency_key="k1", proposal_id="p", user_id="u",
                            action_type="place_order", status="SUCCEEDED"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_seller_publish_records_execution(orch, db, fresh_env):
    chat(orch, db, "idem-s", "list a water bottle at PKR 500 with stock 20", role="seller")
    r = chat(orch, db, "idem-s", "approve")
    assert r.listing.status == "PUBLISHED"
    execs = db.query(models.Execution).filter_by(user_id="idem-s").all()
    assert len(execs) == 1
    assert execs[0].action_type == "publish_listing"
    assert execs[0].result_ref == r.listing.marketplace_listing_id


def test_failed_execution_is_not_recorded(orch, db, fresh_env):
    """A FAILED execution leaves no idempotency row, so a fresh proposal can retry."""

    class FailingClient(MockShopeeClient):
        def place_order(self, **kw):
            raise MarketplaceError("boom")

    dependencies.set_shopee_client(FailingClient())
    chat(orch, db, "idem-f", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "idem-f", "buy the first one")
    pid = r.proposal.id

    outcome = order_service.approve_and_execute(db, pid, "idem-f")
    assert not outcome.ok
    assert order_service.get_proposal_row(db, pid).status == "FAILED"
    assert db.query(models.Execution).count() == 0
