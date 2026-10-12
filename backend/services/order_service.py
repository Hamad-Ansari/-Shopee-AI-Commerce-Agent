import uuid

from backend.database import models
from backend.dependencies import get_policy_engine, get_product_service, get_shopee_client
from backend.exceptions import (
    ApprovalInvalidError,
    MarketplaceError,
    MarketplaceTimeoutError,
    PolicyViolationError,
    ProductNotFoundError,
    ProposalNotFoundError,
)
from backend.schemas.chat import ActionOutcome
from backend.schemas.orders import OrderView
from backend.schemas.proposals import ActionType, ProposalStatus, ProposalView
from backend.services import approval_service, audit_service
from backend.services.state_machine import transition


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def get_proposal_row(db, proposal_id: str):
    return db.get(models.Proposal, proposal_id)


def list_orders(db, user_id: str) -> list[OrderView]:
    rows = (
        db.query(models.Order)
        .filter_by(user_id=user_id)
        .order_by(models.Order.created_at.desc())
        .all()
    )
    return [OrderView.from_row(r) for r in rows]


def get_order_status(db, user_id: str) -> ActionOutcome:
    # If a previous execution timed out (UNKNOWN), resolve it via a status check first.
    unk = (
        db.query(models.Proposal)
        .filter_by(user_id=user_id, status=ProposalStatus.UNKNOWN.value)
        .order_by(models.Proposal.created_at.desc())
        .first()
    )
    if unk:
        return check_status(db, unk.id)
    order = (
        db.query(models.Order)
        .filter_by(user_id=user_id)
        .order_by(models.Order.created_at.desc())
        .first()
    )
    if not order:
        return ActionOutcome(ok=True, message="You have no orders yet.")
    return ActionOutcome(
        ok=True,
        message=(
            f"Your latest order {order.marketplace_order_id or order.id} is "
            f"{order.status} (PKR {order.total_pkr:,.0f})."
        ),
        order=OrderView.from_row(order),
    )


# --------------------------------------------------------------------------- #
# PREPARE (no execution)
# --------------------------------------------------------------------------- #
def prepare_order_proposal(db, user, product_id: str, quantity: int = 1, version: int = 1) -> models.Proposal:
    """Validate + create an order proposal. Never places an order."""
    policy = get_policy_engine()
    ps = get_product_service()
    policy.check_can_buy(user)
    policy.check_quantity(quantity)
    product = ps.check_availability(product_id, quantity)
    shipping = float(product.get("shipping_fee_pkr", 0))
    total = float(product["price_pkr"]) * quantity + shipping
    policy.check_spending(user, total)

    payload = approval_service.order_payload(product, quantity, shipping, total)
    proposal = models.Proposal(
        id=uuid.uuid4().hex,
        user_id=user.id,
        action_type=ActionType.PLACE_ORDER.value,
        status=ProposalStatus.REQUESTED.value,
        items=payload["items"],
        shipping_fee_pkr=shipping,
        total_pkr=total,
        payload_hash=approval_service.compute_payload_hash(payload),
        version=version,
        idempotency_key=uuid.uuid4().hex,
    )
    db.add(proposal)
    db.flush()
    transition(proposal, ProposalStatus.VALIDATED)
    transition(proposal, ProposalStatus.PENDING_APPROVAL)
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_PROPOSAL_CREATED,
        user_id=user.id,
        proposal_id=proposal.id,
        action_type=proposal.action_type,
        total_pkr=total,
    )
    return proposal


# --------------------------------------------------------------------------- #
# EXECUTE (executor node only — requires a valid approval)
# --------------------------------------------------------------------------- #
def _current_order_payload(proposal):
    ps = get_product_service()
    item = proposal.items[0]
    product = ps.get(item["product_id"])
    shipping = float(product.get("shipping_fee_pkr", 0))
    total = float(product["price_pkr"]) * item["quantity"] + shipping
    return approval_service.order_payload(product, item["quantity"], shipping, total), product, total


def _find_execution(db, idempotency_key: str):
    """Persistent idempotency: has this key ever executed successfully?"""
    return db.query(models.Execution).filter_by(idempotency_key=idempotency_key).first()


def _record_execution(db, proposal, result_ref: str) -> None:
    """Record a successful execution so the key can never execute twice — even across restarts."""
    if _find_execution(db, proposal.idempotency_key):
        return
    db.add(
        models.Execution(
            id=uuid.uuid4().hex,
            idempotency_key=proposal.idempotency_key,
            proposal_id=proposal.id,
            user_id=proposal.user_id,
            action_type=proposal.action_type,
            status=ProposalStatus.SUCCEEDED.value,
            result_ref=result_ref,
        )
    )
    db.flush()


def approve_and_execute(db, proposal_id: str, user_id: str) -> ActionOutcome:
    proposal = db.get(models.Proposal, proposal_id)
    if not proposal:
        raise ProposalNotFoundError("Proposal not found.")
    if proposal.action_type != ActionType.PLACE_ORDER.value:
        raise ProposalNotFoundError("This proposal is not an order proposal.")

    # Idempotent at the service level: approving twice never creates a second order.
    if proposal.status == ProposalStatus.SUCCEEDED.value:
        order = db.query(models.Order).filter_by(proposal_id=proposal.id).first()
        return ActionOutcome(
            ok=True,
            message=(
                f"✅ Already confirmed — order "
                f"{order.marketplace_order_id if order else proposal.result_ref}. "
                "No new order was created."
            ),
            proposal=ProposalView.from_row(proposal),
            order=OrderView.from_row(order) if order else None,
        )
    if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
        return ActionOutcome(
            ok=False,
            message=f"This proposal is {proposal.status} and cannot be approved now.",
            proposal=ProposalView.from_row(proposal),
        )

    # Persistence-layer idempotency: this key already executed once (even if the
    # proposal state was lost/reset) — replay the stored outcome, never re-execute.
    existing_exec = _find_execution(db, proposal.idempotency_key)
    if existing_exec:
        audit_service.audit(
            db,
            audit_service.EVENT_EXECUTION_REPLAYED,
            user_id=user_id,
            proposal_id=proposal.id,
            idempotency_key=proposal.idempotency_key,
        )
        order = db.query(models.Order).filter_by(proposal_id=proposal.id).first()
        return ActionOutcome(
            ok=True,
            message=(
                f"✅ This action was already executed ({existing_exec.result_ref}). "
                "No new order was created."
            ),
            proposal=ProposalView.from_row(proposal),
            order=OrderView.from_row(order) if order else None,
        )

    user = db.get(models.User, user_id)
    item = proposal.items[0]

    # Revalidate against live marketplace data BEFORE binding the approval.
    try:
        current_payload, _product, _total = _current_order_payload(proposal)
    except ProductNotFoundError:
        transition(proposal, ProposalStatus.REJECTED, note="product no longer available")
        db.commit()
        audit_service.audit(
            db,
            audit_service.EVENT_APPROVAL_INVALID,
            user_id=user_id,
            proposal_id=proposal.id,
            reason="product unavailable",
        )
        return ActionOutcome(
            ok=False,
            message="❌ That product is no longer available.",
            proposal=ProposalView.from_row(proposal),
        )

    if approval_service.compute_payload_hash(current_payload) != proposal.payload_hash:
        # Price, quantity, seller or shipping changed: the old approval is invalid.
        # Supersede with a fresh proposal and ask again.
        transition(proposal, ProposalStatus.REJECTED, note="superseded: payload changed")
        db.commit()
        audit_service.audit(
            db,
            audit_service.EVENT_APPROVAL_INVALID,
            user_id=user_id,
            proposal_id=proposal.id,
            reason="payload changed since proposal",
        )
        try:
            new_proposal = prepare_order_proposal(
                db, user, item["product_id"], item["quantity"], version=proposal.version + 1
            )
        except PolicyViolationError as e:
            audit_service.audit(
                db, audit_service.EVENT_POLICY_BLOCKED, user_id=user_id, reason=str(e)
            )
            return ActionOutcome(
                ok=False,
                message=f"⚠️ The details changed and the updated order would break a rule: {e}",
            )
        return ActionOutcome(
            ok=True,
            needs_approval=True,
            message=(
                "⚠️ Details changed since you saw this proposal (price/stock/shipping). "
                f"I prepared an updated proposal v{new_proposal.version} — total "
                f"PKR {new_proposal.total_pkr:,.0f}. Reply APPROVE to confirm or REJECT to cancel."
            ),
            proposal=ProposalView.from_row(new_proposal),
        )

    approval = approval_service.create_approval(db, proposal, user_id)
    try:
        approval_service.verify_approval(approval, proposal, user_id, current_payload)
    except ApprovalInvalidError as e:
        audit_service.audit(
            db,
            audit_service.EVENT_APPROVAL_INVALID,
            user_id=user_id,
            proposal_id=proposal.id,
            reason=str(e),
        )
        return ActionOutcome(
            ok=False,
            needs_approval=True,
            message=f"⚠️ {e}",
            proposal=ProposalView.from_row(proposal),
        )

    client = get_shopee_client()
    if not client.is_authorized:
        audit_service.audit(
            db,
            audit_service.EVENT_INTEGRATION_UNAVAILABLE,
            user_id=user_id,
            proposal_id=proposal.id,
        )
        return ActionOutcome(
            ok=False,
            needs_approval=True,
            message=(
                "✅ Approved, but no authorised marketplace integration is connected yet "
                "(set SHOPEE_SHOPEE_CLIENT=official with your Shopee Open Platform "
                "credentials). Nothing was executed — your proposal stays pending."
            ),
            proposal=ProposalView.from_row(proposal),
        )

    transition(proposal, ProposalStatus.APPROVED)
    db.commit()
    audit_service.audit(
        db, audit_service.EVENT_PROPOSAL_APPROVED, user_id=user_id, proposal_id=proposal.id
    )
    transition(proposal, ProposalStatus.REVALIDATED)
    db.commit()
    audit_service.audit(
        db, audit_service.EVENT_REVALIDATED, user_id=user_id, proposal_id=proposal.id
    )
    transition(proposal, ProposalStatus.EXECUTING)
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_EXECUTION_STARTED,
        user_id=user_id,
        proposal_id=proposal.id,
        idempotency_key=proposal.idempotency_key,
    )

    try:
        result = client.place_order(
            idempotency_key=proposal.idempotency_key,
            product_id=item["product_id"],
            quantity=item["quantity"],
            buyer_id=user_id,
        )
    except MarketplaceTimeoutError:
        # Outcome unknown: record UNKNOWN and NEVER blindly retry — check status instead.
        transition(proposal, ProposalStatus.UNKNOWN, note="marketplace timeout")
        db.commit()
        audit_service.audit(
            db, audit_service.EVENT_EXECUTION_UNKNOWN, user_id=user_id, proposal_id=proposal.id
        )
        return ActionOutcome(
            ok=False,
            message=(
                "⚠️ The marketplace timed out — the outcome is NOT confirmed. I have not "
                "submitted it again; ask me to check the order status."
            ),
            proposal=ProposalView.from_row(proposal),
        )
    except MarketplaceError as e:
        transition(proposal, ProposalStatus.FAILED, note=str(e))
        db.commit()
        audit_service.audit(
            db,
            audit_service.EVENT_EXECUTION_FAILED,
            user_id=user_id,
            proposal_id=proposal.id,
            error=str(e),
        )
        return ActionOutcome(
            ok=False,
            message=f"❌ The order failed: {e}",
            proposal=ProposalView.from_row(proposal),
        )

    order = models.Order(
        id=uuid.uuid4().hex,
        user_id=user_id,
        proposal_id=proposal.id,
        idempotency_key=proposal.idempotency_key,
        items=proposal.items,
        total_pkr=proposal.total_pkr,
        status=result.get("status", "CONFIRMED"),
        marketplace_order_id=result.get("order_id"),
    )
    db.add(order)
    proposal.result_ref = result.get("order_id")
    _record_execution(db, proposal, result.get("order_id"))
    transition(proposal, ProposalStatus.SUCCEEDED)
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_EXECUTION_SUCCEEDED,
        user_id=user_id,
        proposal_id=proposal.id,
        order_id=result.get("order_id"),
    )
    return ActionOutcome(
        ok=True,
        message=(
            f"✅ Order confirmed! {result.get('order_id')} — {item['quantity']}x "
            f"{item['title']}, total PKR {proposal.total_pkr:,.0f} including shipping."
        ),
        proposal=ProposalView.from_row(proposal),
        order=OrderView.from_row(order),
    )


def reject(db, proposal_id: str, user_id: str) -> ActionOutcome:
    proposal = db.get(models.Proposal, proposal_id)
    if not proposal:
        raise ProposalNotFoundError("Proposal not found.")
    if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
        return ActionOutcome(
            ok=False,
            message=f"This proposal is {proposal.status}; only a pending proposal can be rejected.",
            proposal=ProposalView.from_row(proposal),
        )
    transition(proposal, ProposalStatus.REJECTED, note="rejected by user")
    db.commit()
    audit_service.audit(
        db, audit_service.EVENT_PROPOSAL_REJECTED, user_id=user_id, proposal_id=proposal.id
    )
    return ActionOutcome(
        ok=True,
        message="❌ Proposal rejected — nothing was executed.",
        proposal=ProposalView.from_row(proposal),
    )


def check_status(db, proposal_id: str) -> ActionOutcome:
    """Resolve an UNKNOWN execution by querying the marketplace — never by resubmitting."""
    proposal = db.get(models.Proposal, proposal_id)
    if not proposal:
        raise ProposalNotFoundError("Proposal not found.")
    if proposal.status != ProposalStatus.UNKNOWN.value:
        return ActionOutcome(
            ok=True,
            message=f"Proposal is {proposal.status} — no status check needed.",
            proposal=ProposalView.from_row(proposal),
        )
    client = get_shopee_client()
    result = client.get_order_status(proposal.idempotency_key)
    audit_service.audit(
        db,
        audit_service.EVENT_STATUS_CHECK,
        user_id=proposal.user_id,
        proposal_id=proposal.id,
        found=bool(result),
    )
    if not result:
        return ActionOutcome(
            ok=False,
            message="⏳ Still not confirmed on the marketplace side. I will not resubmit — check again shortly.",
            proposal=ProposalView.from_row(proposal),
        )
    order = db.query(models.Order).filter_by(proposal_id=proposal.id).first()
    if not order:
        order = models.Order(
            id=uuid.uuid4().hex,
            user_id=proposal.user_id,
            proposal_id=proposal.id,
            idempotency_key=proposal.idempotency_key,
            items=proposal.items,
            total_pkr=proposal.total_pkr,
            status=result.get("status", "CONFIRMED"),
            marketplace_order_id=result.get("order_id"),
        )
        db.add(order)
    proposal.result_ref = result.get("order_id")
    _record_execution(db, proposal, result.get("order_id"))
    transition(proposal, ProposalStatus.SUCCEEDED, note="resolved via status check")
    db.commit()
    return ActionOutcome(
        ok=True,
        message=(
            f"✅ Status checked: your order {result.get('order_id')} is "
            f"{result.get('status', 'CONFIRMED')}. It was submitted exactly once."
        ),
        proposal=ProposalView.from_row(proposal),
        order=OrderView.from_row(order),
    )
