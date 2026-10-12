import uuid

import pytest

from backend.database import models
from backend.exceptions import ApprovalInvalidError
from backend.services import approval_service


def _payload(price=100.0):
    product = {
        "id": "P1",
        "title": "Thing",
        "price_pkr": price,
        "seller": "S",
        "shipping_fee_pkr": 50,
    }
    return approval_service.order_payload(product, 2, 50, price * 2 + 50)


def _proposal(db, version=1):
    p = models.Proposal(
        id=uuid.uuid4().hex,
        user_id="u1",
        action_type="place_order",
        status="PENDING_APPROVAL",
        items=[],
        payload_hash=approval_service.compute_payload_hash(_payload(100)),
        version=version,
        idempotency_key=uuid.uuid4().hex,
    )
    db.add(p)
    db.commit()
    return p


def test_approval_binding_and_invalidation(db):
    proposal = _proposal(db)
    approval = approval_service.create_approval(db, proposal, "u1")

    # Happy path verifies.
    approval_service.verify_approval(approval, proposal, "u1", _payload(100))

    # Any change to price invalidates the approval.
    with pytest.raises(ApprovalInvalidError):
        approval_service.verify_approval(approval, proposal, "u1", _payload(150))

    # Only the requesting user can approve.
    with pytest.raises(ApprovalInvalidError):
        approval_service.verify_approval(approval, proposal, "u2", _payload(100))

    # A version bump invalidates the old approval.
    proposal.version = 2
    db.commit()
    with pytest.raises(ApprovalInvalidError):
        approval_service.verify_approval(approval, proposal, "u1", _payload(100))


def test_create_approval_is_idempotent_per_user(db):
    proposal = _proposal(db)
    a1 = approval_service.create_approval(db, proposal, "u1")
    a2 = approval_service.create_approval(db, proposal, "u1")
    assert a1.id == a2.id
    assert db.query(models.Approval).count() == 1
