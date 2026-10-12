from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user
from backend.database import models
from backend.database.session import get_db
from backend.dependencies import get_orchestrator
from backend.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Main conversational endpoint — the same backend the WhatsApp webhook will use in Phase 4.

    The acting user is the authenticated user; a user_id in the body must match it.
    """
    if req.user_id and req.user_id != user.id:
        raise HTTPException(status_code=403, detail="user_id does not match the authenticated user.")
    return get_orchestrator().handle_message(db, user.id, req.message, req.name, req.role)
