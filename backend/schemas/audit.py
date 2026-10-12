from datetime import datetime

from pydantic import BaseModel, Field


class AuditEventView(BaseModel):
    id: str
    user_id: str | None = None
    event_type: str
    proposal_id: str | None = None
    details: dict = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "AuditEventView":
        return cls(
            id=row.id,
            user_id=row.user_id,
            event_type=row.event_type,
            proposal_id=row.proposal_id,
            details=row.details or {},
            created_at=row.created_at,
        )
