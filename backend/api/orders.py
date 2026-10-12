from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user
from backend.database import models
from backend.database.session import get_db
from backend.schemas.chat import ActionOutcome
from backend.schemas.orders import OrderView
from backend.schemas.proposals import ActionType, ProposalView
from backend.services import order_service, seller_service, user_service

router = APIRouter(tags=["orders"])


def _get_owned_proposal(db, proposal_id: str, user: models.User) -> models.Proposal:
    proposal = order_service.get_proposal_row(db, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.user_id != user.id:
        raise HTTPException(status_code=403, detail="This proposal belongs to a different user.")
    return proposal


@router.get("/orders")
def list_orders(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"orders": order_service.list_orders(db, user.id)}


@router.get("/orders/{order_id}")
def get_order(order_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderView.from_row(order)


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProposalView.from_row(_get_owned_proposal(db, proposal_id, user))


@router.post("/proposals/{proposal_id}/approve", response_model=ActionOutcome)
def approve_proposal(proposal_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    proposal = _get_owned_proposal(db, proposal_id, user)
    if proposal.action_type == ActionType.PUBLISH_LISTING.value:
        outcome = seller_service.approve_and_publish(db, proposal_id, user.id)
    else:
        outcome = order_service.approve_and_execute(db, proposal_id, user.id)
    _sync_session(db, user.id, outcome)
    return outcome


@router.post("/proposals/{proposal_id}/reject", response_model=ActionOutcome)
def reject_proposal(proposal_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    proposal = _get_owned_proposal(db, proposal_id, user)
    if proposal.action_type == ActionType.PUBLISH_LISTING.value:
        outcome = seller_service.reject_publish(db, proposal_id, user.id)
    else:
        outcome = order_service.reject(db, proposal_id, user.id)
    _sync_session(db, user.id, outcome)
    return outcome


@router.post("/proposals/{proposal_id}/check-status", response_model=ActionOutcome)
def check_proposal_status(proposal_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_owned_proposal(db, proposal_id, user)
    return order_service.check_status(db, proposal_id)


def _sync_session(db, user_id: str, outcome: ActionOutcome) -> None:
    row = user_service.get_or_create_session(db, user_id)
    # Copy in/out: SQLAlchemy does not detect in-place mutation of a JSON column.
    data = dict(row.data or {})
    if outcome.needs_approval and outcome.proposal:
        data["pending_proposal_id"] = outcome.proposal.id
    else:
        data.pop("pending_proposal_id", None)
    row.data = dict(data)
    db.commit()
