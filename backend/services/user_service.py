from datetime import datetime

from backend.config import get_settings
from backend.database import models


def get_or_create_user(db, user_id: str, name: str = "", role: str = "") -> models.User:
    user = db.get(models.User, user_id)
    if not user:
        user = models.User(
            id=user_id,
            name=name or user_id,
            role=role or "buyer",
            spending_limit_pkr=get_settings().default_spending_limit_pkr,
        )
        db.add(user)
        db.commit()
        return user
    changed = False
    if name and user.name != name:
        user.name = name
        changed = True
    if role and user.role != role:
        user.role = role
        changed = True
    if changed:
        db.commit()
    return user


def get_or_create_session(db, user_id: str) -> models.Session:
    session = db.get(models.Session, user_id)
    if not session:
        session = models.Session(user_id=user_id, data={})
        db.add(session)
        db.commit()
    return session


def load_session_data(db, user_id: str) -> dict:
    """Fresh copy of the session state (graph nodes always reload from the DB)."""
    return dict(get_or_create_session(db, user_id).data or {})


def save_session_data(db, user_id: str, data: dict) -> None:
    row = get_or_create_session(db, user_id)
    # Assign a fresh dict: SQLAlchemy does not detect in-place mutation of a JSON column.
    row.data = dict(data)
    row.updated_at = datetime.utcnow()
    db.commit()
