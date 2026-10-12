from datetime import datetime

from pydantic import BaseModel


class UserView(BaseModel):
    """Public user profile — never exposes password_hash or api_key_hash."""

    id: str
    name: str
    role: str
    spending_limit_pkr: float
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "UserView":
        return cls(
            id=row.id,
            name=row.name,
            role=row.role,
            spending_limit_pkr=row.spending_limit_pkr,
            created_at=row.created_at,
        )
