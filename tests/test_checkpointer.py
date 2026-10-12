"""Phase 6: the persistent LangGraph checkpointer — paused approvals survive restarts.

Simulates a process restart by building a brand-new Orchestrator on the same
checkpointer file: a thread paused at PENDING_APPROVAL must resume and execute.
"""

from backend import dependencies
from backend.agents.graph import Orchestrator
from backend.database import models


def chat(orch, db, user_id, message):
    return orch.handle_message(db, user_id, message)


def test_paused_approval_survives_a_restart(orch, db, fresh_env):
    chat(orch, db, "ckpt-1", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "ckpt-1", "buy the first one")
    assert r.needs_approval is True
    proposal_id = r.proposal.id

    # --- "restart": a brand-new Orchestrator on the same checkpointer file ---
    orch2 = Orchestrator()
    resumed_state = orch2.graph.get_state({"configurable": {"thread_id": orch2.thread_id_for("ckpt-1")}})
    assert resumed_state.next == ("await_approval",)  # still paused, state intact

    r = chat(orch2, db, "ckpt-1", "approve")
    assert r.order is not None
    assert "confirmed" in r.message.lower()
    assert db.query(models.Order).count() == 1
    proposal = db.get(models.Proposal, proposal_id)
    assert proposal.status == "SUCCEEDED"


def test_paused_rejection_survives_a_restart(orch, db, fresh_env):
    chat(orch, db, "ckpt-2", "Find wireless earbuds under PKR 2,000")
    r = chat(orch, db, "ckpt-2", "buy the first one")
    assert r.needs_approval
    proposal_id = r.proposal.id

    orch2 = Orchestrator()  # "restart"
    r = chat(orch2, db, "ckpt-2", "reject")
    assert "rejected" in r.message.lower()
    assert db.query(models.Order).count() == 0
    assert db.get(models.Proposal, proposal_id).status == "REJECTED"


def test_checkpointer_is_persistent_not_inmemory(orch, db, fresh_env):
    # The Phase 6 checkpointer must be the sqlite one (InMemorySaver would lose
    # the thread on a new Orchestrator instance).
    from langgraph.checkpoint.sqlite import SqliteSaver

    assert isinstance(orch.checkpointer, SqliteSaver)
    # and a second orchestrator sees the first one's thread state
    chat(orch, db, "ckpt-3", "hi")
    orch2 = Orchestrator()
    state = orch2.graph.get_state({"configurable": {"thread_id": orch2.thread_id_for("ckpt-3")}})
    assert state is not None and state.values  # thread exists in the shared file


def test_singleton_reset_gives_a_fresh_orchestrator(db, fresh_env):
    first = dependencies.get_orchestrator()
    dependencies.set_orchestrator(None)
    second = dependencies.get_orchestrator()
    assert first is not second
