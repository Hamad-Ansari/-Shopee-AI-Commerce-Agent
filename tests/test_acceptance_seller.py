"""Seller-side acceptance tests (pack §6, items 7, 8, 10) plus listing flows."""

from backend import dependencies
from backend.database import models
from backend.integrations.shopee_client import OfficialShopeeClient
from backend.services import order_service


def chat(orch, db, user_id, message, role="seller"):
    return orch.handle_message(db, user_id, message, role=role)


# Acceptance 7: a seller listing missing a price is sent back with a correction request.
def test_listing_missing_price_asks_for_correction(orch, db, fresh_env):
    r = chat(orch, db, "seller-t7", "list a water bottle")
    assert "price" in r.message.lower()
    assert db.query(models.Listing).count() == 0
    assert db.query(models.Proposal).count() == 0
    events = {e.event_type for e in db.query(models.AuditEvent).all()}
    assert "listing_validation_failed" in events


# Acceptance 8: without an authorised API, a listing stays a local draft and the user is told.
def test_unauthorized_api_keeps_listing_as_local_draft(orch, db, fresh_env):
    dependencies.set_shopee_client(OfficialShopeeClient())  # is_authorized = False

    r = chat(orch, db, "seller-t8", "list a water bottle at PKR 500 with stock 20")
    assert r.needs_approval is True
    assert r.listing is not None
    pid = r.proposal.id

    r2 = chat(orch, db, "seller-t8", "approve")
    assert "local draft" in r2.message.lower()
    listing = db.get(models.Listing, r.listing.id)
    assert listing.status == "DRAFT"
    assert listing.marketplace_listing_id is None
    assert order_service.get_proposal_row(db, pid).status == "PENDING_APPROVAL"
    events = {e.event_type for e in db.query(models.AuditEvent).all()}
    assert "integration_unavailable" in events


def test_publish_with_mock_client(orch, db, fresh_env):
    r = chat(orch, db, "seller-t9", "list a water bottle at PKR 500 with stock 20")
    assert r.needs_approval is True

    r2 = chat(orch, db, "seller-t9", "approve")
    assert r2.listing is not None
    assert r2.listing.status == "PUBLISHED"
    assert r2.listing.marketplace_listing_id.startswith("MOCK-LIST-")
    assert order_service.get_proposal_row(db, r.proposal.id).status == "SUCCEEDED"
    events = {e.event_type for e in db.query(models.AuditEvent).all()}
    assert "listing_published" in events


def test_seller_reject_keeps_draft(orch, db, fresh_env):
    r = chat(orch, db, "seller-t10", "list a water bottle at PKR 500 with stock 20")
    r2 = chat(orch, db, "seller-t10", "reject")
    assert "draft" in r2.message.lower()
    listing = db.get(models.Listing, r.listing.id)
    assert listing.status == "DRAFT"
    assert listing.marketplace_listing_id is None
    assert order_service.get_proposal_row(db, r.proposal.id).status == "REJECTED"


def test_listing_validation_requires_title(orch, db, fresh_env):
    r = chat(orch, db, "seller-t11", "list at PKR 500 with stock 20")
    assert "title" in r.message.lower()
    assert db.query(models.Listing).count() == 0


def test_stock_check_and_sales_summary(orch, db, fresh_env):
    r = chat(orch, db, "seller-t12", "check stock of power bank")
    assert "power bank" in r.message.lower()
    assert "stock" in r.message.lower()

    r2 = chat(orch, db, "seller-t12", "my sales summary")
    assert "summary" in r2.message.lower()


# Acceptance 10 (seller side): listing flow leaves a full audit trail.
def test_seller_flow_leaves_audit_record(orch, db, fresh_env):
    chat(orch, db, "seller-t13", "list a water bottle at PKR 500 with stock 20")
    chat(orch, db, "seller-t13", "approve")
    events = {
        e.event_type for e in db.query(models.AuditEvent).filter_by(user_id="seller-t13").all()
    }
    expected = {
        "intent",
        "listing_drafted",
        "proposal_created",
        "approval",
        "proposal_approved",
        "revalidated",
        "execution_started",
        "listing_published",
    }
    assert expected <= events
