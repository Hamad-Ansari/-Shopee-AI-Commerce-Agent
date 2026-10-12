from datetime import datetime

from backend.exceptions import InvalidTransitionError
from backend.schemas.proposals import ProposalStatus

# REQUESTED -> VALIDATED -> PENDING_APPROVAL -> (REJECTED | APPROVED) -> REVALIDATED
# -> EXECUTING -> (SUCCEEDED | FAILED | UNKNOWN)
TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.REQUESTED: {ProposalStatus.VALIDATED},
    ProposalStatus.VALIDATED: {ProposalStatus.PENDING_APPROVAL},
    ProposalStatus.PENDING_APPROVAL: {ProposalStatus.APPROVED, ProposalStatus.REJECTED},
    ProposalStatus.APPROVED: {ProposalStatus.REVALIDATED, ProposalStatus.PENDING_APPROVAL},
    ProposalStatus.REJECTED: set(),
    ProposalStatus.REVALIDATED: {ProposalStatus.EXECUTING},
    ProposalStatus.EXECUTING: {
        ProposalStatus.SUCCEEDED,
        ProposalStatus.FAILED,
        ProposalStatus.UNKNOWN,
    },
    # UNKNOWN is resolved by a status check (never by resubmitting), then becomes terminal.
    ProposalStatus.UNKNOWN: {ProposalStatus.SUCCEEDED, ProposalStatus.FAILED},
    ProposalStatus.SUCCEEDED: set(),
    ProposalStatus.FAILED: set(),
}


def transition(proposal, to_status: ProposalStatus, note: str | None = None) -> None:
    current = ProposalStatus(proposal.status)
    if to_status not in TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"Cannot move proposal {proposal.id} from {current.value} to {to_status.value}"
        )
    proposal.status = to_status.value
    proposal.updated_at = datetime.utcnow()
    if note:
        proposal.note = note
