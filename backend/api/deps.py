from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.database.session import get_db
from backend.services import auth_service


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Resolve 'Authorization: Bearer <api_key>' to a user, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Use 'Bearer <api_key>'.",
        )
    api_key = authorization[7:].strip()
    user = auth_service.authenticate_by_api_key(db, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    return user
