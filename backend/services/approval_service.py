import hashlib
import json
import uuid

from backend.database import models
from backend.exceptions import ApprovalInvalidError
from backend.services import audit_service


def compute_payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def order_payload(product: dict, quantity: int, shipping_fee_pkr: float, total_pkr: float) -> dict:
    return {
        "action_type": "place_order",
        "items": [
            {
                "product_id": product["id"],
                "title": product["title"],
                "quantity": quantity,
                "unit_price_pkr": float(product["price_pkr"]),
                "seller": product["seller"],
            }
        ],
        "shipping_fee_pkr": shipping_fee_pkr,
        "total_pkr": total_pkr,
    }


def listing_payload(fields: dict) -> dict:
    return {
        "action_type": "publish_listing",
        "listing": {
            "title": fields["title"],
            "category": fields.get("category") or "General",
            "price_pkr": float(fields["price_pkr"]),
            "stock": int(fields["stock"]),
            "description": fields.get("description", ""),
        },
    }


def create_approval(db, proposal, user_id: str) -> models.Approval:
    """Bind an approval to proposal_id + proposal_version + user_id + payload_hash.

    Idempotent: re-approving the same proposal by the same user reuses the binding.
    """
    existing = (
        db.query(models.Approval)
        .filter_by(proposal_id=proposal.id, user_id=user_id)
        .first()
    )
    if existing:
        return existing
    approval = models.Approval(
        id=uuid.uuid4().hex,
        proposal_id=proposal.id,
        proposal_version=proposal.version,
        user_id=user_id,
        payload_hash=proposal.payload_hash,
        idempotency_key=proposal.idempotency_key,
    )
    db.add(approval)
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_APPROVAL,
        user_id=user_id,
        proposal_id=proposal.id,
        payload_hash=proposal.payload_hash,
    )
    return approval


def verify_approval(approval, proposal, user_id: str, current_payload: dict) -> None:
    """Any change to price, quantity, seller or shipping invalidates the approval."""
    if approval.proposal_id != proposal.id:
        raise ApprovalInvalidError("This approval is bound to a different proposal.")
    if approval.proposal_version != proposal.version:
        raise ApprovalInvalidError("The proposal version changed — please approve the new version.")
    if approval.user_id != user_id:
        raise ApprovalInvalidError("Only the user who requested this action can approve it.")
    if approval.payload_hash != compute_payload_hash(current_payload):
        raise ApprovalInvalidError(
            "The approval no longer matches the current price, quantity, seller or "
            "shipping. Please review and approve again."
        )
