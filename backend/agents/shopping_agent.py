from backend.agents import messages as fmt
from backend.agents.tools import ToolRegistry
from backend.dependencies import get_product_service
from backend.schemas.chat import ChatResponse, product_card
from backend.schemas.intents import Intent, IntentType
from backend.schemas.proposals import ProposalView
from backend.services import audit_service

_ORDINAL_IDX = {
    "first": 0, "1st": 0, "one": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
}


class ShoppingAgent:
    """Buyer mode: search, compare, prepare order proposals, handle approvals."""

    mode = "buyer"

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    def handle(self, intent: Intent, db, user, session: dict) -> ChatResponse:
        t = intent.type
        if t == IntentType.SEARCH:
            return self._search(intent, db, user, session)
        if t == IntentType.BUY:
            return self._buy(intent, db, user, session)
        if t == IntentType.APPROVE:
            return self._approve(db, user, session)
        if t == IntentType.REJECT:
            return self._reject(db, user, session)
        if t == IntentType.ORDER_STATUS:
            return self._order_status(db, user)
        if t == IntentType.HELP:
            return ChatResponse(message=fmt.HELP_MESSAGE)
        if intent.params.get("hint") == "pending_proposal":
            return ChatResponse(message=fmt.PENDING_NUDGE)
        return ChatResponse(message=fmt.CLARIFY_MESSAGE)

    def _search(self, intent, db, user, session) -> ChatResponse:
        results = self.tools.call(
            "search_products",
            db,
            query=intent.params.get("query", ""),
            max_price_pkr=intent.params.get("max_price_pkr"),
            limit=3,
        )
        session["last_results"] = [p["id"] for p in results]
        audit_service.audit(
            db,
            audit_service.EVENT_SEARCH,
            user_id=user.id,
            query=intent.params.get("query", ""),
            hits=len(results),
        )
        if not results:
            return ChatResponse(
                message="😕 No products matched that search. Try different keywords or a bigger budget."
            )
        lines = [f"🔎 Top matches for \"{intent.params.get('query') or 'your search'}\":"]
        for i, p in enumerate(results, 1):
            lines.append(fmt.card_lines(p, i))
        return ChatResponse(
            message="\n\n".join(lines),
            products=[product_card(p) for p in results],
            action="search",
        )

    def _buy(self, intent, db, user, session) -> ChatResponse:
        target = intent.params.get("target", "last")
        quantity = int(intent.params.get("quantity", 1))
        product = self._resolve_product(target, session)
        if product is None:
            return ChatResponse(
                message="😕 I couldn't find that product. Try a search first — e.g. "
                "\"find wireless earbuds under PKR 2,000\"."
            )
        proposal = self.tools.call(
            "prepare_order_proposal",
            db,
            user=user,
            product_id=product["id"],
            quantity=quantity,
        )
        session["pending_proposal_id"] = proposal.id
        msg = fmt.format_order_proposal(proposal) + "\n\nReply APPROVE to confirm or REJECT to cancel."
        return ChatResponse(
            message=msg,
            proposal=ProposalView.from_row(proposal),
            needs_approval=True,
            action="proposal",
        )

    def _resolve_product(self, target: str, session: dict):
        ps = get_product_service()
        last = session.get("last_results") or []
        if target in _ORDINAL_IDX:
            idx = _ORDINAL_IDX[target]
            return ps.get(last[idx]) if idx < len(last) else None
        if target in {"it", "this", "that", "last", "the one"}:
            return ps.get(last[-1]) if last else None
        results = ps.search(target, limit=1)
        return results[0] if results else None

    def _approve(self, db, user, session) -> ChatResponse:
        pid = session.get("pending_proposal_id")
        if not pid:
            return ChatResponse(message="There's nothing waiting for approval right now.")
        outcome = self.tools.call(
            "execute_place_order",
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
            order=outcome.order,
            needs_approval=outcome.needs_approval,
            action="approve",
        )

    def _reject(self, db, user, session) -> ChatResponse:
        pid = session.get("pending_proposal_id")
        if not pid:
            return ChatResponse(message="There's nothing to reject right now.")
        outcome = self.tools.call(
            "reject_order_proposal", db, proposal_id=pid, user_id=user.id
        )
        session.pop("pending_proposal_id", None)
        return ChatResponse(
            message=outcome.message, proposal=outcome.proposal, action="reject"
        )

    def _order_status(self, db, user) -> ChatResponse:
        outcome = self.tools.call("get_order_status", db, user_id=user.id)
        return ChatResponse(
            message=outcome.message, order=outcome.order, action="order_status"
        )
