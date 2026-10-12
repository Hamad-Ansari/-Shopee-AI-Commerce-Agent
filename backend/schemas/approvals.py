from datetime import datetime

from pydantic import BaseModel


class ApprovalView(BaseModel):
    id: str
    proposal_id: str
    proposal_version: int
    user_id: str
    payload_hash: str
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "ApprovalView":
        return cls(
            id=row.id,
            proposal_id=row.proposal_id,
            proposal_version=row.proposal_version,
            user_id=row.user_id,
            payload_hash=row.payload_hash,
            created_at=row.created_at,
        )
