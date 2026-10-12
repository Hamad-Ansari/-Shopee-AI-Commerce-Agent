"""Phase 3: the LangGraph orchestrator and the human-in-the-loop interrupt."""

import json

from conftest import FakeOllamaClient

from backend import dependencies
from backend.agents.llm import OllamaProvider, RuleBasedProvider
from backend.database import models
from backend.services import order_service


def chat(orch, db, user_id, message, role=""):
    return orch.handle_message(db, user_id, message, role=role)


def thread_state(orch, user_id):
    return orch.graph.get_state({"configurable": {"thread_id": orch.thread_id_for(user_id)}})


def test_proposal_pauses_the_thread_and_approval_resumes_it(orch, db, fresh_env):
    chat(orch, db, "graph-t1", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "graph-t1", "buy the first one")
    assert r.needs_approval is True

    # The thread is paused at the PENDING_APPROVAL interrupt.
    snapshot = thread_state(orch, "graph-t1")
    assert snapshot.next == ("await_approval",)

    # The next message resumes the thread and executes through the approval.
    r = chat(orch, db, "graph-t1", "approve")
    assert r.order is not None
    assert "confirmed" in r.message.lower()
    assert db.query(models.Order).count() == 1

    # The thread completed; the next message starts a fresh run.
    assert thread_state(orch, "graph-t1").next == ()


def test_resume_with_reject_cancels_the_proposal(orch, db, fresh_env):
    chat(orch, db, "graph-t2", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "graph-t2", "buy the first one")
    assert r.needs_approval
    assert thread_state(orch, "graph-t2").next == ("await_approval",)

    r = chat(orch, db, "graph-t2", "reject")
    assert "rejected" in r.message.lower()
    assert db.query(models.Order).count() == 0
    proposal = order_service.get_proposal_row(db, r.proposal.id if r.proposal else "")
    # the original proposal is the rejected one
    proposals = db.query(models.Proposal).filter_by(user_id="graph-t2").all()
    assert all(p.status == "REJECTED" for p in proposals)
    assert thread_state(orch, "graph-t2").next == ()


def test_resume_with_an_unrelated_message(orch, db, fresh_env):
    chat(orch, db, "graph-t3", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "graph-t3", "buy the first one")
    assert r.needs_approval

    # The user changes their mind and searches instead — the resumed thread handles it.
    r = chat(orch, db, "graph-t3", "find over-ear headphones")
    assert r.products
    assert any("Headphones" in p.title for p in r.products)
    assert thread_state(orch, "graph-t3").next == ()


def test_seller_listing_pauses_and_resumes(orch, db, fresh_env):
    r = chat(orch, db, "graph-t4", "list a water bottle at PKR 500 with stock 20", role="seller")
    assert r.needs_approval
    assert thread_state(orch, "graph-t4").next == ("await_approval",)

    r = chat(orch, db, "graph-t4", "approve")
    assert r.listing is not None
    assert r.listing.status == "PUBLISHED"
    assert thread_state(orch, "graph-t4").next == ()


def test_threads_are_isolated_per_user(orch, db, fresh_env):
    chat(orch, db, "graph-t5a", "Find wireless earbuds under PKR 2,000")
    chat(orch, db, "graph-t5a", "buy the first one")
    assert thread_state(orch, "graph-t5a").next == ("await_approval",)

    # Another user is completely unaffected by the paused thread.
    r = chat(orch, db, "graph-t5b", "find wireless earbuds under PKR 2,000")
    assert r.products
    assert thread_state(orch, "graph-t5b").next == ()

    # And the first user can still resume their own thread.
    r = chat(orch, db, "graph-t5a", "approve")
    assert r.order is not None


def test_graph_records_the_full_audit_trail(orch, db, fresh_env):
    chat(orch, db, "graph-t6", "Find wireless earbuds under PKR 2,000")
    chat(orch, db, "graph-t6", "buy the first one")
    chat(orch, db, "graph-t6", "approve")
    events = {
        e.event_type for e in db.query(models.AuditEvent).filter_by(user_id="graph-t6").all()
    }
    assert {
        "intent",
        "search",
        "proposal_created",
        "approval",
        "proposal_approved",
        "revalidated",
        "execution_started",
        "execution_succeeded",
    } <= events


def test_graph_runs_with_the_llm_provider(orch, db, fresh_env):
    """End-to-end through the graph with OllamaProvider (fake client) producing intents."""
    responses = [
        json.dumps({"type": "search", "mode": "buyer", "params": {"query": "wireless earbuds", "max_price_pkr": 2000}}),
        json.dumps({"type": "buy", "mode": "buyer", "params": {"target": "first", "quantity": 1}}),
        json.dumps({"type": "approve", "mode": "any", "params": {}}),
    ]
    client = FakeOllamaClient(responses)
    dependencies.set_llm_provider(OllamaProvider(model="test-model", client=client))

    r1 = chat(orch, db, "llm-t1", "Find wireless earbuds under PKR 2,000")
    assert r1.products
    assert thread_state(orch, "llm-t1").next == ()

    r2 = chat(orch, db, "llm-t1", "buy the first one")
    assert r2.needs_approval
    assert thread_state(orch, "llm-t1").next == ("await_approval",)

    r3 = chat(orch, db, "llm-t1", "approve")
    assert r3.order is not None
    assert db.query(models.Order).count() == 1
    assert len(client.calls) == 3  # one LLM call per message


def test_llm_fallback_is_audited(orch, db, fresh_env):
    client = FakeOllamaClient([ValueError("model server exploded"), ValueError("again")])
    dependencies.set_llm_provider(
        OllamaProvider(model="test-model", client=client, fallback=RuleBasedProvider())
    )
    r = chat(orch, db, "llm-t2", "Find wireless earbuds under PKR 2,000")
    assert r.products  # fell back to rules and still answered
    events = {
        e.event_type for e in db.query(models.AuditEvent).filter_by(user_id="llm-t2").all()
    }
    assert "llm_fallback" in events
