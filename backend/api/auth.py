from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user
from backend.database import models
from backend.database.session import get_db
from backend.schemas.users import UserView
from backend.services import audit_service, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    user_id: str = Field(min_length=1)
    password: str = Field(min_length=6)
    name: str = ""
    role: str = "buyer"


@router.post("/register", status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Create a user. The API key is returned exactly once — store it safely."""
    if db.get(models.User, req.user_id):
        raise HTTPException(status_code=409, detail="User ID already taken.")
    user, api_key = auth_service.register(db, req.user_id, req.password, req.name, req.role)
    audit_service.audit(db, audit_service.EVENT_USER_REGISTERED, user_id=user.id, role=user.role)
    return {
        "user_id": user.id,
        "name": user.name,
        "role": user.role,
        "api_key": api_key,
        "message": "Save this API key — it is shown only once. "
        "Send it as 'Authorization: Bearer <api_key>'.",
    }


@router.get("/me", response_model=UserView)
def me(user: models.User = Depends(get_current_user)):
    return UserView.from_row(user)
