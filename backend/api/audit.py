from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user
from backend.database import models
from backend.database.session import get_db
from backend.schemas.audit import AuditEventView

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit_events(
    event_type: str | None = None,
    limit: int = 50,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Query your own audit trail (every intent, proposal, approval, execution, error)."""
    q = db.query(models.AuditEvent).filter_by(user_id=user.id)
    if event_type:
        q = q.filter(models.AuditEvent.event_type == event_type)
    rows = q.order_by(models.AuditEvent.created_at.desc()).limit(limit).all()
    return {"events": [AuditEventView.from_row(r) for r in rows]}
