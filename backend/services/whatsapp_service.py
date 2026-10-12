"""WhatsApp glue (Phase 4): Meta webhook payloads in, reply specs out.

- Session mapping: the sender's E.164 phone number IS the orchestrator user_id, so a
  WhatsApp conversation maps 1:1 onto the same per-user thread/sessions as the web UI.
- Inbound: text messages go through the orchestrator (same backend). Interactive
  button taps carry "approve:<proposal_id>" / "reject:<proposal_id>" payloads and go
  straight to the approval-bound executor (with an ownership check) — the tap itself
  is the explicit approval.
- Outbound: specs are plain dicts; the webhook endpoint sends them via the configured
  WhatsAppClient. Proposals include interactive Approve/Reject buttons.
"""

import hashlib
import hmac
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from backend.database import models
from backend.dependencies import get_orchestrator
from backend.schemas.chat import ChatResponse
from backend.schemas.proposals import ActionType
from backend.services import audit_service, order_service, seller_service, user_service

APPROVE_PREFIX = "approve:"
REJECT_PREFIX = "reject:"


def verify_meta_signature(body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 header: 'sha256=<hex hmac of raw body>'."""
    if not signature_header or not signature_header.startswith("sha256=") or not app_secret:
        return False
    digest = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature_header[len("sha256="):])


# --------------------------------------------------------------------------- #
# Inbound
# --------------------------------------------------------------------------- #
def process_webhook(db, payload: dict) -> list[dict]:
    """Parse a Meta webhook payload and return outbound reply specs."""
    outbound: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            for message in value.get("messages", []) or []:
                outbound.extend(_handle_message(db, message))
            # value.get("statuses") are delivery/read receipts — audited, not answered.
            for _status in value.get("statuses", []) or []:
                audit_service.audit(db, audit_service.EVENT_WA_INBOUND, details={"kind": "status"})
    return outbound


def _is_duplicate(db, message: dict) -> bool:
    """At-most-once processing: Meta re-delivers the same payload on retries.

    The wamid is unique per message, so one row per id in wa_messages dedups
    retries. The insert+commit is atomic (primary key) — a concurrent duplicate
    loses the race, rolls back, and is skipped + audited. Messages without an id
    (status receipts etc.) are never deduped. The executor's idempotency store
    remains the real guard for consequential actions.
    """
    message_id = message.get("id")
    if not message_id:
        return False
    db.add(
        models.WaMessage(
            message_id=message_id,
            user_id=message.get("from"),
            wa_type=message.get("type"),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        audit_service.audit(
            db,
            audit_service.EVENT_WA_DUPLICATE,
            user_id=message.get("from"),
            wa_message_id=message_id,
        )
        return True
    return False


def purge_old_wa_messages(db, days: int = 7) -> int:
    """Retention helper: delete dedup rows older than `days` (run on a schedule)."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = db.query(models.WaMessage).filter(models.WaMessage.created_at < cutoff).delete()
    db.commit()
    return deleted


def _handle_message(db, message: dict) -> list[dict]:
    phone = message.get("from")
    if not phone:
        return []
    if _is_duplicate(db, message):
        return []
    user_id = phone  # session mapping: phone number == orchestrator user_id
    msg_type = message.get("type")
    audit_service.audit(
        db,
        audit_service.EVENT_WA_INBOUND,
        user_id=user_id,
        wa_message_id=message.get("id"),
        wa_type=msg_type,
    )
    user_service.get_or_create_user(db, user_id, name=f"WhatsApp {phone}")

    if msg_type == "text":
        text = (message.get("text") or {}).get("body", "")
        reply = get_orchestrator().handle_message(db, user_id, text)
        return [_reply_to_spec(phone, reply)]

    if msg_type in ("interactive", "button"):
        return _handle_button(db, user_id, phone, message)

    # Unsupported type (image, audio, reaction, ...) — answer politely once.
    reply = ChatResponse(
        user_id=user_id,
        message="📱 I can read text messages and button taps — send me text like "
        "\"find wireless earbuds under PKR 2,000\".",
    )
    return [_reply_to_spec(phone, reply)]


def _handle_button(db, user_id: str, phone: str, message: dict) -> list[dict]:
    interactive = message.get("interactive") or {}
    button_reply = interactive.get("button_reply") or message.get("button") or {}
    payload_id = button_reply.get("id") or button_reply.get("payload") or ""
    audit_service.audit(
        db, audit_service.EVENT_WA_BUTTON, user_id=user_id, button_id=payload_id
    )

    if payload_id.startswith(APPROVE_PREFIX):
        outcome = _execute_button_action(db, user_id, payload_id[len(APPROVE_PREFIX):], approve=True)
    elif payload_id.startswith(REJECT_PREFIX):
        outcome = _execute_button_action(db, user_id, payload_id[len(REJECT_PREFIX):], approve=False)
    else:
        outcome = None

    if outcome is None:
        reply = ChatResponse(
            user_id=user_id,
            message="🤔 I don't recognise that button. Send me a text message instead.",
        )
    else:
        reply = ChatResponse(user_id=user_id, message=outcome.message)
    return [_reply_to_spec(phone, reply)]


def _execute_button_action(db, user_id: str, proposal_id: str, approve: bool):
    """Run a button tap through the approval-bound executor (ownership-checked)."""
    proposal = order_service.get_proposal_row(db, proposal_id)
    if not proposal or proposal.user_id != user_id:
        from backend.schemas.chat import ActionOutcome

        return ActionOutcome(
            ok=False,
            message="🚫 That proposal doesn't exist or belongs to a different chat.",
        )
    if approve:
        if proposal.action_type == ActionType.PUBLISH_LISTING.value:
            outcome = seller_service.approve_and_publish(db, proposal_id, user_id)
        else:
            outcome = order_service.approve_and_execute(db, proposal_id, user_id)
    else:
        if proposal.action_type == ActionType.PUBLISH_LISTING.value:
            outcome = seller_service.reject_publish(db, proposal_id, user_id)
        else:
            outcome = order_service.reject(db, proposal_id, user_id)
    _sync_pending_proposal(db, user_id, outcome)
    return outcome


def _sync_pending_proposal(db, user_id: str, outcome) -> None:
    session = user_service.load_session_data(db, user_id)
    if outcome.needs_approval and outcome.proposal:
        session["pending_proposal_id"] = outcome.proposal.id
    else:
        session.pop("pending_proposal_id", None)
    user_service.save_session_data(db, user_id, session)


# --------------------------------------------------------------------------- #
# Outbound
# --------------------------------------------------------------------------- #
def _reply_to_spec(phone: str, reply: ChatResponse) -> dict:
    """Turn an orchestrator reply into an outbound spec (text, or text + buttons)."""
    if reply.needs_approval and reply.proposal:
        pid = reply.proposal.id
        return {
            "to": phone,
            "type": "interactive",
            "body": reply.message,
            "buttons": [
                {"id": f"{APPROVE_PREFIX}{pid}", "title": "Approve"},
                {"id": f"{REJECT_PREFIX}{pid}", "title": "Reject"},
            ],
        }
    return {"to": phone, "type": "text", "body": reply.message}


def send_spec(client, db, spec: dict) -> None:
    """Send one outbound spec via the WhatsApp client and audit it."""
    if spec["type"] == "interactive":
        client.send_interactive_buttons(spec["to"], spec["body"], spec["buttons"])
    else:
        client.send_text(spec["to"], spec["body"])
    audit_service.audit(
        db, audit_service.EVENT_WA_OUTBOUND, user_id=spec["to"], kind=spec["type"]
    )
