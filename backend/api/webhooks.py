import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database.session import SessionLocal, get_db
from backend.dependencies import get_whatsapp_client
from backend.exceptions import IntegrationUnavailableError
from backend.services import audit_service
from backend.services.whatsapp_service import process_webhook, send_spec, verify_meta_signature

router = APIRouter(tags=["webhooks"])


@router.get("/webhooks/whatsapp")
def whatsapp_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta webhook verification handshake (hub.mode=verify&hub.verify_token=...&hub.challenge=...)."""
    settings = get_settings()
    if hub_mode == "verify" and hub_verify_token and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


def _send_specs(client, specs: list[dict]) -> None:
    """Background task: deliver outbound replies AFTER the 200 is sent.

    Runs with its own DB session (the request's session is being torn down) and
    audits wa_outbound per spec. An unauthorized client is audited, never faked.
    """
    db = SessionLocal()
    try:
        for spec in specs:
            try:
                send_spec(client, db, spec)
            except IntegrationUnavailableError:
                # No authorised WhatsApp integration: the reply is still audited, the
                # sender gets nothing — same "approved but unexecuted" honesty rule.
                audit_service.audit(
                    db,
                    audit_service.EVENT_INTEGRATION_UNAVAILABLE,
                    user_id=spec["to"],
                    reason="whatsapp_client_unauthorized",
                )
    finally:
        db.close()


@router.post("/webhooks/whatsapp")
async def whatsapp_inbound(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Meta inbound webhook: verify the signature, parse + process, ack 200, send async."""
    body = await request.body()
    settings = get_settings()

    # Signature verification (enforced when an app secret is configured).
    if settings.whatsapp_app_secret:
        if not verify_meta_signature(
            body, request.headers.get("x-hub-signature-256"), settings.whatsapp_app_secret
        ):
            audit_service.audit(db, audit_service.EVENT_WA_SIGNATURE_REJECTED)
            raise HTTPException(status_code=403, detail="Invalid X-Hub-Signature-256")

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Inbound processing (dedup, orchestrator, executor) stays synchronous and fast;
    # the actual sends move to a background task so Meta gets a quick 200 and never
    # retries just because a send was slow.
    specs = process_webhook(db, payload)

    client = get_whatsapp_client()
    if specs:
        background_tasks.add_task(_send_specs, client, specs)
    return {"status": "ok", "replies": len(specs)}
