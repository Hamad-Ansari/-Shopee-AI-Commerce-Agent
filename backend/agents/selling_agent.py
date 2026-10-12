from backend.agents import messages as fmt
from backend.agents.tools import ToolRegistry
from backend.dependencies import get_product_service
from backend.schemas.chat import ChatResponse
from backend.schemas.intents import Intent, IntentType
from backend.schemas.listings import ListingView
from backend.schemas.proposals import ProposalView
from backend.services import audit_service, seller_service


class SellingAgent:
    """Seller mode: draft listings, stock checks, publish approvals, sales summaries."""

    mode = "seller"

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    def handle(self, intent: Intent, db, user, session: dict) -> ChatResponse:
        t = intent.type
        if t == IntentType.SELLER_LISTING:
            return self._new_listing(intent, db, user, session)
        if t == IntentType.APPROVE:
            return self._approve(db, user, session)
        if t == IntentType.REJECT:
            return self._reject(db, user, session)
        if t == IntentType.STOCK_CHECK:
            return self._stock_check(intent, db, user)
        if t == IntentType.SALES_SUMMARY:
            return self._sales_summary(db, user)
        if t == IntentType.HELP:
            return ChatResponse(message=fmt.HELP_MESSAGE)
        if intent.params.get("hint") == "pending_proposal":
            return ChatResponse(message=fmt.PENDING_NUDGE)
        return ChatResponse(message=fmt.CLARIFY_MESSAGE)

    def _new_listing(self, intent, db, user, session) -> ChatResponse:
        fields = intent.params
        errors = seller_service.validate_listing(fields)
        if errors:
            audit_service.audit(
                db,
                audit_service.EVENT_LISTING_VALIDATION_FAILED,
                user_id=user.id,
                errors=errors,
            )
            return ChatResponse(
                message=(
                    "📝 I need a bit more info to draft your listing — please provide: "
                    + ", ".join(errors)
                    + ".\nExample: \"list a water bottle at PKR 500 with stock 20\""
                )
            )
        draft = self.tools.call("create_listing_draft", db, seller_id=user.id, fields=fields)
        proposal = seller_service.prepare_publish_proposal(db, user, draft)
        session["pending_proposal_id"] = proposal.id
        msg = (
            fmt.format_listing_preview(draft)
            + "\n\nReply APPROVE to publish or REJECT to keep it as a draft."
        )
        return ChatResponse(
            message=msg,
            proposal=ProposalView.from_row(proposal),
            listing=ListingView.from_row(draft),
            needs_approval=True,
            action="listing_proposal",
        )

    def _approve(self, db, user, session) -> ChatResponse:
        pid = session.get("pending_proposal_id")
        if not pid:
            return ChatResponse(message="There's nothing waiting for approval right now.")
        outcome = self.tools.call(
            "execute_publish_listing",
            db,
            approved_by=user.id,
            proposal_id=pid,
            user_id=user.id,
        )
        if outcome.needs_approval and outcome.proposal:
            session["pending_proposal_id"] = outcome.proposal.id
        else:
            session.pop("pending_proposal_id", None)
        return ChatResponse(
            message=outcome.message,
            proposal=outcome.proposal,
            listing=outcome.listing,
            needs_approval=outcome.needs_approval,
            action="approve",
        )

    def _reject(self, db, user, session) -> ChatResponse:
        pid = session.get("pending_proposal_id")
        if not pid:
            return ChatResponse(message="There's nothing to reject right now.")
        outcome = self.tools.call(
            "reject_listing_proposal", db, proposal_id=pid, user_id=user.id
        )
        session.pop("pending_proposal_id", None)
        return ChatResponse(
            message=outcome.message,
            proposal=outcome.proposal,
            listing=outcome.listing,
            action="reject",
        )

    def _stock_check(self, intent, db, user) -> ChatResponse:
        ps = get_product_service()
        results = ps.search(intent.params.get("query", ""), limit=3)
        if not results:
            return ChatResponse(message="😕 I couldn't find that product in the catalog.")
        p = results[0]
        return ChatResponse(
            message=(
                f"📦 \"{p['title']}\" — {p['stock']} unit(s) in stock at "
                f"{fmt.format_price(p['price_pkr'])} (seller: {p['seller']})."
            ),
            action="stock_check",
        )

    def _sales_summary(self, db, user) -> ChatResponse:
        outcome = seller_service.sales_summary(db, user.id)
        return ChatResponse(message=outcome.message, action="sales_summary")
