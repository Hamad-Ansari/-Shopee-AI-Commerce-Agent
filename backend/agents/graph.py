"""LangGraph orchestrator (Phase 3).

Graph:

    START -> extract_intent -> agent -> (await_approval | END)
                                   await_approval -> END

- extract_intent: LLM (Ollama) structured intent extraction into the shared Intent
  schema, with the rule-based provider as a safe fallback.
- agent: routes buyer vs seller intents to the shopping/selling agents (which call
  the typed tool registry: READ / PREPARE only — never EXECUTE directly).
- await_approval: the human-in-the-loop interrupt. When a proposal is created the
  thread pauses at PENDING_APPROVAL via langgraph.types.interrupt(); the next user
  message resumes the thread with Command(resume=...), and the resumed message is
  processed (approve -> executor node, reject -> cancel, anything else -> new intent).

The DB session is injected into nodes through a ContextVar (never through graph
state/config, which the checkpointer would try to serialize). Phase 6: the
checkpointer is persistent (SqliteSaver at SHOPEE_CHECKPOINTER_PATH), so a thread
paused at PENDING_APPROVAL survives a process restart; it falls back to
InMemorySaver only if the sqlite checkpointer package is unavailable.
"""

import logging
import sqlite3
from contextvars import ContextVar
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

logger = logging.getLogger(__name__)

from backend.agents.selling_agent import SellingAgent
from backend.agents.shopping_agent import ShoppingAgent
from backend.agents.tools import build_tool_registry
from backend.database import models
from backend.dependencies import get_llm_provider, get_policy_engine
from backend.exceptions import (
    ApprovalInvalidError,
    IntegrationUnavailableError,
    MarketplaceTimeoutError,
    PolicyViolationError,
    ProductNotFoundError,
    RateLimitError,
)
from backend.schemas.chat import ChatResponse
from backend.schemas.intents import Intent, IntentType
from backend.schemas.proposals import ActionType
from backend.services import audit_service, order_service, user_service

# Per-request DB session, injected around graph.invoke() and read by nodes.
_db_var: ContextVar = ContextVar("shopbot_db")


class AgentState(TypedDict, total=False):
    message: str
    user_id: str
    role: str
    intent: dict
    reply: dict
    session: dict
    awaiting_approval: bool


def _build_checkpointer():
    """Persistent LangGraph checkpointer (Phase 6): paused approvals survive restarts.

    Uses SqliteSaver at SHOPEE_CHECKPOINTER_PATH when langgraph-checkpoint-sqlite is
    available; falls back to InMemorySaver (with a loud warning) otherwise.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        from backend.config import get_settings

        path = get_settings().checkpointer_path
        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        logger.info("LangGraph checkpointer: persistent sqlite at %s", path)
        return saver
    except Exception as e:  # ImportError or unwritable path — degrade, never crash
        logger.warning("Persistent checkpointer unavailable (%s) — using InMemorySaver; "
                       "paused approvals will NOT survive restarts.", e)
        return InMemorySaver()


class Orchestrator:
    """Message orchestrator backed by a LangGraph state machine (one thread per user)."""

    def __init__(self):
        self.tools = build_tool_registry()
        self.shopping_agent = ShoppingAgent(self.tools)
        self.selling_agent = SellingAgent(self.tools)
        self.checkpointer = _build_checkpointer()
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ #
    # Graph construction
    # ------------------------------------------------------------------ #
    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("extract_intent", self._extract_intent_node)
        builder.add_node("agent", self._agent_node)
        builder.add_node("await_approval", self._await_approval_node)
        builder.add_edge(START, "extract_intent")
        builder.add_edge("extract_intent", "agent")
        builder.add_conditional_edges(
            "agent", self._after_agent, {"await": "await_approval", "end": END}
        )
        builder.add_edge("await_approval", END)
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def thread_id_for(user_id: str) -> str:
        return f"user:{user_id}"

    @staticmethod
    def _after_agent(state: AgentState) -> str:
        return "await" if state.get("awaiting_approval") else "end"

    # ------------------------------------------------------------------ #
    # Nodes
    # ------------------------------------------------------------------ #
    def _extract_intent_node(self, state: AgentState) -> dict:
        db = _db_var.get()
        provider = get_llm_provider()
        session = user_service.load_session_data(db, state["user_id"])
        intent = provider.extract_intent(state["message"], session, state.get("role", "buyer"))
        audit_service.audit(
            db,
            audit_service.EVENT_INTENT,
            user_id=state["user_id"],
            intent=intent.type.value,
            mode=intent.mode,
            provider=provider.name,
        )
        if getattr(provider, "last_used_fallback", False):
            audit_service.audit(
                db,
                audit_service.EVENT_LLM_FALLBACK,
                user_id=state["user_id"],
                reason="llm_unavailable_or_invalid_output",
            )
        return {"intent": intent.model_dump(mode="json")}

    def _agent_node(self, state: AgentState) -> dict:
        db = _db_var.get()
        intent = Intent.model_validate(state["intent"])
        user = user_service.get_or_create_user(db, state["user_id"])
        session = user_service.load_session_data(db, state["user_id"])
        reply = self._dispatch(intent, db, user, session)
        user_service.save_session_data(db, state["user_id"], session)
        return {
            "reply": reply.model_dump(mode="json"),
            "session": session,
            "awaiting_approval": reply.needs_approval,
        }

    def _await_approval_node(self, state: AgentState) -> dict:
        """Human-in-the-loop: pause at PENDING_APPROVAL; the next user message resumes."""
        db = _db_var.get()
        user_id = state["user_id"]
        pending_proposal = (state.get("reply") or {}).get("proposal") or {}
        resumed = interrupt(
            {"kind": "pending_approval", "proposal_id": pending_proposal.get("id")}
        )
        message = (resumed or {}).get("message", "")
        if not message:
            return {}  # resumed without a message — keep waiting, reply unchanged

        user = user_service.get_or_create_user(db, user_id)
        session = user_service.load_session_data(db, user_id)
        provider = get_llm_provider()
        intent = provider.extract_intent(message, session, user.role)
        audit_service.audit(
            db,
            audit_service.EVENT_INTENT,
            user_id=user_id,
            intent=intent.type.value,
            mode=intent.mode,
            provider=provider.name,
        )
        if getattr(provider, "last_used_fallback", False):
            audit_service.audit(
                db,
                audit_service.EVENT_LLM_FALLBACK,
                user_id=user_id,
                reason="llm_unavailable_or_invalid_output",
            )
        reply = self._dispatch(intent, db, user, session)
        user_service.save_session_data(db, user_id, session)
        return {"reply": reply.model_dump(mode="json"), "session": session}

    # ------------------------------------------------------------------ #
    # Routing (buyer vs seller)
    # ------------------------------------------------------------------ #
    def _dispatch(self, intent: Intent, db, user: models.User, session: dict) -> ChatResponse:
        if intent.type in (IntentType.SELLER_LISTING, IntentType.STOCK_CHECK, IntentType.SALES_SUMMARY):
            return self.selling_agent.handle(intent, db, user, session)
        if intent.type in (IntentType.APPROVE, IntentType.REJECT):
            pid = session.get("pending_proposal_id")
            proposal = order_service.get_proposal_row(db, pid) if pid else None
            if proposal and proposal.action_type == ActionType.PUBLISH_LISTING.value:
                return self.selling_agent.handle(intent, db, user, session)
        return self.shopping_agent.handle(intent, db, user, session)

    # ------------------------------------------------------------------ #
    # Public API (unchanged signature)
    # ------------------------------------------------------------------ #
    def handle_message(self, db, user_id: str, message: str, name: str = "", role: str = "") -> ChatResponse:
        user = user_service.get_or_create_user(db, user_id, name, role)
        try:
            get_policy_engine().check_rate_limit(user.id)
        except RateLimitError:
            audit_service.audit(db, audit_service.EVENT_RATE_LIMITED, user_id=user.id)
            return ChatResponse(
                user_id=user_id,
                message="🙏 You're messaging a bit fast — please wait a moment and try again.",
            )

        token = _db_var.set(db)
        try:
            reply = self._safe_invoke(db, user, message)
        finally:
            _db_var.reset(token)
        reply.user_id = user_id
        return reply

    def _safe_invoke(self, db, user: models.User, message: str) -> ChatResponse:
        config = {"configurable": {"thread_id": self.thread_id_for(user.id)}}
        try:
            snapshot = self.graph.get_state(config)
            if snapshot.next:
                # Thread is paused at the PENDING_APPROVAL interrupt: resume it.
                result = self.graph.invoke(Command(resume={"message": message}), config)
            else:
                result = self.graph.invoke(
                    {"message": message, "user_id": user.id, "role": user.role}, config
                )
            return ChatResponse.model_validate(
                result.get("reply")
                or {"message": "😅 Something went wrong on my side — please try again."}
            )
        except PolicyViolationError as e:
            audit_service.audit(
                db, audit_service.EVENT_POLICY_BLOCKED, user_id=user.id, reason=str(e)
            )
            return ChatResponse(user_id=user.id, message=f"⛔ {e}")
        except ProductNotFoundError:
            return ChatResponse(
                user_id=user.id,
                message="😕 I couldn't find that product. Try a search — e.g. "
                "\"find wireless earbuds under PKR 2,000\".",
            )
        except ApprovalInvalidError as e:
            return ChatResponse(user_id=user.id, message=f"⚠️ {e}")
        except MarketplaceTimeoutError:
            return ChatResponse(
                user_id=user.id,
                message="⚠️ The marketplace timed out, so the outcome is not confirmed. "
                "I have not submitted it again — ask me to check the order status.",
            )
        except IntegrationUnavailableError as e:
            return ChatResponse(user_id=user.id, message=f"🔌 {e}")
        except Exception as e:  # noqa: BLE001 — last-resort guard, always audited
            audit_service.audit(db, audit_service.EVENT_ERROR, user_id=user.id, error=str(e))
            return ChatResponse(
                user_id=user.id, message="😅 Something went wrong on my side — please try again."
            )
