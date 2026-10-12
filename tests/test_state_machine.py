import uuid

import pytest

from backend.database import models
from backend.exceptions import InvalidTransitionError
from backend.schemas.proposals import ProposalStatus
from backend.services.state_machine import transition


def make_proposal(status: ProposalStatus) -> models.Proposal:
    return models.Proposal(
        id=uuid.uuid4().hex,
        user_id="u",
        action_type="place_order",
        status=status.value,
        items=[],
        idempotency_key=uuid.uuid4().hex,
    )


def test_invalid_transition_raises(db):
    p = make_proposal(ProposalStatus.REQUESTED)
    db.add(p)
    db.commit()
    with pytest.raises(InvalidTransitionError):
        transition(p, ProposalStatus.EXECUTING)


def test_happy_path(db):
    p = make_proposal(ProposalStatus.REQUESTED)
    db.add(p)
    db.commit()
    for nxt in (
        ProposalStatus.VALIDATED,
        ProposalStatus.PENDING_APPROVAL,
        ProposalStatus.APPROVED,
        ProposalStatus.REVALIDATED,
        ProposalStatus.EXECUTING,
        ProposalStatus.SUCCEEDED,
    ):
        transition(p, nxt)
        db.commit()
    assert p.status == ProposalStatus.SUCCEEDED.value


def test_unknown_resolves_via_status_check(db):
    p = make_proposal(ProposalStatus.REQUESTED)
    db.add(p)
    db.commit()
    for nxt in (
        ProposalStatus.VALIDATED,
        ProposalStatus.PENDING_APPROVAL,
        ProposalStatus.APPROVED,
        ProposalStatus.REVALIDATED,
        ProposalStatus.EXECUTING,
        ProposalStatus.UNKNOWN,
        ProposalStatus.SUCCEEDED,
    ):
        transition(p, nxt)
        db.commit()
    assert p.status == ProposalStatus.SUCCEEDED.value


def test_terminal_states_reject_further_transitions(db):
    for terminal in (ProposalStatus.REJECTED, ProposalStatus.SUCCEEDED, ProposalStatus.FAILED):
        p = make_proposal(terminal)
        db.add(p)
        db.commit()
        with pytest.raises(InvalidTransitionError):
            transition(p, ProposalStatus.EXECUTING)
