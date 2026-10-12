import uuid

from backend.database import models
from backend.dependencies import get_policy_engine, get_shopee_client
from backend.exceptions import (
    ApprovalInvalidError,
    MarketplaceError,
    MarketplaceTimeoutError,
    ProposalNotFoundError,
)
from backend.schemas.chat import ActionOutcome
from backend.schemas.listings import ListingStatus, ListingView
from backend.schemas.proposals import ActionType, ProposalStatus, ProposalView
from backend.services import approval_service, audit_service
from backend.services.state_machine import transition


# --------------------------------------------------------------------------- #
# Listing validation + drafts (PREPARE)
# --------------------------------------------------------------------------- #
def validate_listing(fields: dict) -> list[str]:
    """Return a list of human-readable missing/invalid field descriptions (empty = valid)."""
    errors = []
    title = (fields.get("title") or "").strip()
    if len(title) < 3:
        errors.append("a title (at least 3 characters)")
    price = fields.get("price_pkr")
    if price is None or float(price) <= 0:
        errors.append("a price in PKR greater than 0")
    stock = fields.get("stock")
    if stock is None or int(stock) < 0:
        errors.append("a stock quantity (0 or more)")
    return errors


def create_draft(db, seller_id: str, fields: dict) -> models.Listing:
    get_policy_engine().check_can_sell(db.get(models.User, seller_id) or models.User(id=seller_id))
    listing = models.Listing(
        id=uuid.uuid4().hex,
        seller_id=seller_id,
        title=fields["title"].strip(),
        category=fields.get("category") or "General",
        price_pkr=float(fields["price_pkr"]),
        stock=int(fields["stock"]),
        description=fields.get("description", ""),
        status=ListingStatus.DRAFT.value,
    )
    db.add(listing)
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_LISTING_DRAFTED,
        user_id=seller_id,
        listing_id=listing.id,
        title=listing.title,
    )
    return listing


def _listing_fields(listing: models.Listing) -> dict:
    return {
        "title": listing.title,
        "category": listing.category,
        "price_pkr": listing.price_pkr,
        "stock": listing.stock,
        "description": listing.description,
    }


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


def prepare_publish_proposal(db, user, listing: models.Listing) -> models.Proposal:
    payload = approval_service.listing_payload(_listing_fields(listing))
    proposal = models.Proposal(
        id=uuid.uuid4().hex,
        user_id=user.id,
        action_type=ActionType.PUBLISH_LISTING.value,
        status=ProposalStatus.REQUESTED.value,
        items=[],
        shipping_fee_pkr=0.0,
        total_pkr=0.0,
        payload_hash=approval_service.compute_payload_hash(payload),
        version=1,
        idempotency_key=uuid.uuid4().hex,
        result_ref=listing.id,
    )
    db.add(proposal)
    db.flush()
    transition(proposal, ProposalStatus.VALIDATED)
    transition(proposal, ProposalStatus.PENDING_APPROVAL)
    listing.proposal_id = proposal.id
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_PROPOSAL_CREATED,
        user_id=user.id,
        proposal_id=proposal.id,
        action_type=proposal.action_type,
        listing_id=listing.id,
    )
    return proposal


# --------------------------------------------------------------------------- #
# EXECUTE (executor node only — requires a valid approval)
# --------------------------------------------------------------------------- #
def approve_and_publish(db, proposal_id: str, user_id: str) -> ActionOutcome:
    proposal = db.get(models.Proposal, proposal_id)
    if not proposal:
        raise ProposalNotFoundError("Proposal not found.")
    if proposal.action_type != ActionType.PUBLISH_LISTING.value:
        raise ProposalNotFoundError("This proposal is not a listing-publication proposal.")

    if proposal.status == ProposalStatus.SUCCEEDED.value:
        listing = db.get(models.Listing, proposal.result_ref) if proposal.result_ref else None
        return ActionOutcome(
            ok=True,
            message=(
                f"✅ Already published — listing "
                f"{listing.marketplace_listing_id if listing else proposal.result_ref}. "
                "No new listing was created."
            ),
            proposal=ProposalView.from_row(proposal),
            listing=ListingView.from_row(listing) if listing else None,
        )
    if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
        return ActionOutcome(
            ok=False,
            message=f"This proposal is {proposal.status} and cannot be approved now.",
            proposal=ProposalView.from_row(proposal),
        )

    # Persistence-layer idempotency: replay the stored outcome, never re-execute.
    existing_exec = _find_execution(db, proposal.idempotency_key)
    if existing_exec:
        audit_service.audit(
            db,
            audit_service.EVENT_EXECUTION_REPLAYED,
            user_id=user_id,
            proposal_id=proposal.id,
            idempotency_key=proposal.idempotency_key,
        )
        listing = db.get(models.Listing, proposal.result_ref) if proposal.result_ref else None
        return ActionOutcome(
            ok=True,
            message=(
                f"✅ This action was already executed ({existing_exec.result_ref}). "
                "No new listing was created."
            ),
            proposal=ProposalView.from_row(proposal),
            listing=ListingView.from_row(listing) if listing else None,
        )

    listing = db.get(models.Listing, proposal.result_ref) if proposal.result_ref else None
    if not listing:
        return ActionOutcome(
            ok=False,
            message="The listing draft for this proposal no longer exists.",
            proposal=ProposalView.from_row(proposal),
        )

    current_payload = approval_service.listing_payload(_listing_fields(listing))
    if approval_service.compute_payload_hash(current_payload) != proposal.payload_hash:
        transition(proposal, ProposalStatus.REJECTED, note="listing changed since proposal")
        db.commit()
        audit_service.audit(
            db,
            audit_service.EVENT_APPROVAL_INVALID,
            user_id=user_id,
            proposal_id=proposal.id,
            reason="listing changed since proposal",
        )
        return ActionOutcome(
            ok=False,
            message="⚠️ The listing details changed after the draft was previewed. Please create a fresh listing.",
            proposal=ProposalView.from_row(proposal),
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
            listing=ListingView.from_row(listing),
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
                "✅ Approved, but no authorised Shopee seller API is connected yet "
                "(set SHOPEE_SHOPEE_CLIENT=official with your Shopee Open Platform "
                "credentials). Your listing is saved as a LOCAL DRAFT — nothing was published."
            ),
            proposal=ProposalView.from_row(proposal),
            listing=ListingView.from_row(listing),
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
        result = client.publish_listing(
            idempotency_key=proposal.idempotency_key,
            listing=_listing_fields(listing),
        )
    except MarketplaceTimeoutError:
        transition(proposal, ProposalStatus.UNKNOWN, note="marketplace timeout")
        db.commit()
        audit_service.audit(
            db, audit_service.EVENT_EXECUTION_UNKNOWN, user_id=user_id, proposal_id=proposal.id
        )
        return ActionOutcome(
            ok=False,
            message=(
                "⚠️ The marketplace timed out — the outcome is NOT confirmed and I will "
                "not resubmit. Ask me to check the listing status."
            ),
            proposal=ProposalView.from_row(proposal),
            listing=ListingView.from_row(listing),
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
            message=f"❌ Publishing failed: {e}",
            proposal=ProposalView.from_row(proposal),
            listing=ListingView.from_row(listing),
        )

    listing.status = ListingStatus.PUBLISHED.value
    listing.marketplace_listing_id = result.get("listing_id")
    _record_execution(db, proposal, result.get("listing_id"))
    transition(proposal, ProposalStatus.SUCCEEDED)
    db.commit()
    audit_service.audit(
        db,
        audit_service.EVENT_LISTING_PUBLISHED,
        user_id=user_id,
        proposal_id=proposal.id,
        listing_id=result.get("listing_id"),
    )
    return ActionOutcome(
        ok=True,
        message=(
            f"✅ Listing published! {result.get('listing_id')} — \"{listing.title}\" is "
            f"now live at PKR {listing.price_pkr:,.0f}."
        ),
        proposal=ProposalView.from_row(proposal),
        listing=ListingView.from_row(listing),
    )


def reject_publish(db, proposal_id: str, user_id: str) -> ActionOutcome:
    proposal = db.get(models.Proposal, proposal_id)
    if not proposal:
        raise ProposalNotFoundError("Proposal not found.")
    if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
        return ActionOutcome(
            ok=False,
            message=f"This proposal is {proposal.status}; only a pending proposal can be rejected.",
            proposal=ProposalView.from_row(proposal),
        )
    transition(proposal, ProposalStatus.REJECTED, note="rejected by seller")
    db.commit()
    audit_service.audit(
        db, audit_service.EVENT_PROPOSAL_REJECTED, user_id=user_id, proposal_id=proposal.id
    )
    listing = db.get(models.Listing, proposal.result_ref) if proposal.result_ref else None
    return ActionOutcome(
        ok=True,
        message="❌ Publication rejected — your listing stays a local draft.",
        proposal=ProposalView.from_row(proposal),
        listing=ListingView.from_row(listing) if listing else None,
    )


def sales_summary(db, seller_id: str) -> ActionOutcome:
    listings = db.query(models.Listing).filter_by(seller_id=seller_id).all()
    published = [l for l in listings if l.status == ListingStatus.PUBLISHED.value]
    drafts = [l for l in listings if l.status == ListingStatus.DRAFT.value]
    live_value = sum(l.price_pkr * l.stock for l in published)
    return ActionOutcome(
        ok=True,
        message=(
            f"📊 Sales summary for {seller_id}: {len(published)} published listing(s), "
            f"{len(drafts)} draft(s). Live inventory value: PKR {live_value:,.0f}. "
            "(Phase 1 uses mock data — real sales analytics arrive with the marketplace "
            "API in Phase 5.)"
        ),
    )
