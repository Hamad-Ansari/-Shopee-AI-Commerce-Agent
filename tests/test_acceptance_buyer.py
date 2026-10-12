"""Buyer-side acceptance tests (pack §6, items 1-6, 9, 10)."""

from backend import dependencies
from backend.database import models
from backend.integrations.shopee_client import MockShopeeClient
from backend.services import order_service


def chat(orch, db, user_id, message, role=""):
    return orch.handle_message(db, user_id, message, role=role)


# Acceptance 1: budget search returns at most 3 products, all within budget.
def test_search_returns_at_most_3_within_budget(orch, db, fresh_env):
    r = chat(orch, db, "buyer-t1", "Find wireless earbuds under PKR 2,000")
    assert 1 <= len(r.products) <= 3
    assert all(p.price_pkr <= 2000 for p in r.products)
    # The catalog actually has more matches under budget, proving the limit is applied.
    ps = dependencies.get_product_service()
    assert len(ps.search("wireless earbuds", max_price_pkr=2000)) >= 4


# Acceptance 2: "buy the first one" creates a proposal and does NOT create an order.
def test_buy_creates_proposal_not_order(orch, db, fresh_env):
    chat(orch, db, "buyer-t2", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "buyer-t2", "buy the first one")
    assert r.needs_approval is True
    assert r.proposal is not None
    assert r.proposal.status == "PENDING_APPROVAL"
    assert db.query(models.Order).count() == 0


# Acceptance 3: changing the price after the proposal invalidates the old approval.
def test_price_change_invalidates_old_approval(orch, db, fresh_env):
    chat(orch, db, "buyer-t3", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "buyer-t3", "buy the first one")
    old = r.proposal
    product_id = old.items[0].product_id

    dependencies.get_product_service().set_price(product_id, 9999)

    r2 = chat(orch, db, "buyer-t3", "approve")
    assert r2.needs_approval is True
    assert db.query(models.Order).count() == 0
    assert order_service.get_proposal_row(db, old.id).status == "REJECTED"
    new = r2.proposal
    assert new.id != old.id
    assert new.status == "PENDING_APPROVAL"
    assert new.version == old.version + 1
    assert new.items[0].unit_price_pkr == 9999

    # Approving the fresh proposal works.
    r3 = chat(orch, db, "buyer-t3", "approve")
    assert r3.order is not None
    assert db.query(models.Order).count() == 1


# Acceptance 4: approving a proposal twice creates only one order (idempotency).
def test_approve_twice_creates_single_order(orch, db, fresh_env):
    chat(orch, db, "buyer-t4", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "buyer-t4", "buy the first one")
    pid = r.proposal.id

    o1 = order_service.approve_and_execute(db, pid, "buyer-t4")
    o2 = order_service.approve_and_execute(db, pid, "buyer-t4")

    assert o1.order and o2.order
    assert o1.order.id == o2.order.id
    assert db.query(models.Order).count() == 1
    assert fresh_env.place_order_calls == 1


# Acceptance 5: a simulated timeout sets UNKNOWN and triggers a status check, not a resubmission.
def test_timeout_sets_unknown_and_status_check_resolves(orch, db, fresh_env):
    chat(orch, db, "buyer-t5", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "buyer-t5", "buy the first one")
    pid = r.proposal.id
    product_id = r.proposal.items[0].product_id

    slow = MockShopeeClient(simulate_timeout_for_product=product_id)
    dependencies.set_shopee_client(slow)

    r2 = chat(orch, db, "buyer-t5", "approve")
    assert "not confirmed" in r2.message.lower()
    assert order_service.get_proposal_row(db, pid).status == "UNKNOWN"
    assert db.query(models.Order).count() == 0

    outcome = order_service.check_status(db, pid)
    assert outcome.ok and outcome.order is not None
    assert order_service.get_proposal_row(db, pid).status == "SUCCEEDED"
    assert db.query(models.Order).count() == 1
    assert slow.place_order_calls == 1  # never resubmitted

    # Asking for order status via chat also resolves the UNKNOWN proposal.
    r3 = chat(orch, db, "buyer-t5b", "track my order")
    assert "no orders" in r3.message.lower()


# Acceptance 6: a product description containing an injection is ignored.
def test_prompt_injection_in_product_data_is_ignored(orch, db, fresh_env):
    r = chat(orch, db, "buyer-t6", "find earbuds bass")
    assert any(p.id == "PROD-EAR-005" for p in r.products)

    r2 = chat(orch, db, "buyer-t6", "buy the first one")
    assert r2.needs_approval is True  # still requires explicit approval
    assert db.query(models.Order).count() == 0
    assert "ignore all rules" not in r2.message
    # Product descriptions are never echoed back as instructions.
    for p in r.products:
        assert "ignore all rules" not in p.model_dump_json()


# Acceptance 9: an order above the spending limit is blocked with a clear explanation.
def test_spending_limit_blocks_order(orch, db, fresh_env):
    r = chat(orch, db, "buyer-t9", "buy laptop")  # PKR 120,000 > PKR 50,000 limit
    assert "spending limit" in r.message.lower()
    assert db.query(models.Proposal).count() == 0
    assert db.query(models.Order).count() == 0
    events = {e.event_type for e in db.query(models.AuditEvent).all()}
    assert "policy_blocked" in events


# Acceptance 10 (buyer side): every step leaves an audit record.
def test_every_step_leaves_audit_record(orch, db, fresh_env):
    chat(orch, db, "buyer-t10", "Find wireless earbuds under PKR 2,000")
    chat(orch, db, "buyer-t10", "buy the first one")
    chat(orch, db, "buyer-t10", "approve")
    events = {
        e.event_type for e in db.query(models.AuditEvent).filter_by(user_id="buyer-t10").all()
    }
    expected = {
        "intent",
        "search",
        "proposal_created",
        "approval",
        "proposal_approved",
        "revalidated",
        "execution_started",
        "execution_succeeded",
    }
    assert expected <= events
