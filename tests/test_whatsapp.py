"""Phase 4: WhatsApp webhook — verification, inbound parsing, buttons, outbound."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from backend import dependencies
from backend.database import models
from backend.integrations.whatsapp_client import MockWhatsAppClient, WhatsAppCloudClient
from backend.exceptions import IntegrationUnavailableError
from backend.main import app
from backend.services import order_service
from backend.services.whatsapp_service import process_webhook, verify_meta_signature

PHONE_A = "923001234567"
PHONE_B = "923007654321"


def text_message(phone, body, msg_id="wamid-1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "PNID"},
                            "messages": [
                                {"from": phone, "id": msg_id, "type": "text", "text": {"body": body}}
                            ],
                        },
                    }
                ],
            }
        ],
    }


def button_message(phone, payload_id, msg_id="wamid-2"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "PNID"},
                            "messages": [
                                {
                                    "from": phone,
                                    "id": msg_id,
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button",
                                        "button_reply": {"id": payload_id, "title": "Approve"},
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def wa_mock():
    return dependencies.get_whatsapp_client()


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def test_meta_verify_ok_and_wrong_token(db, fresh_env):
    with TestClient(app) as c:
        r = c.get(
            "/webhooks/whatsapp",
            params={"hub.mode": "verify", "hub.verify_token": "change-me", "hub.challenge": "chal-123"},
        )
        assert r.status_code == 200 and r.text == "chal-123"
        r = c.get(
            "/webhooks/whatsapp",
            params={"hub.mode": "verify", "hub.verify_token": "wrong", "hub.challenge": "x"},
        )
        assert r.status_code == 403


def test_verify_meta_signature_pure():
    body = b'{"object":"whatsapp_business_account"}'
    secret = "app-secret"
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, good, secret)
    assert not verify_meta_signature(body, "sha256=deadbeef", secret)
    assert not verify_meta_signature(body, None, secret)
    assert not verify_meta_signature(body, good, "")  # no secret configured -> never valid here
    assert not verify_meta_signature(body, "md5=abc", secret)


# --------------------------------------------------------------------------- #
# Inbound text -> orchestrator
# --------------------------------------------------------------------------- #
def test_inbound_text_provisions_user_and_replies(orch, db, fresh_env):
    specs = process_webhook(db, text_message(PHONE_A, "hi"))
    assert len(specs) == 1
    assert specs[0]["to"] == PHONE_A
    assert specs[0]["type"] == "text"
    # session mapping: the phone number is the user id
    user = db.get(models.User, PHONE_A)
    assert user is not None
    events = {e.event_type for e in db.query(models.AuditEvent).all()}
    assert "wa_inbound" in events


def test_search_and_buy_via_whatsapp(orch, db, fresh_env):
    specs = process_webhook(db, text_message(PHONE_A, "find wireless earbuds under PKR 2,000"))
    assert "PKR" in specs[0]["body"]

    specs = process_webhook(db, text_message(PHONE_A, "buy the first one", msg_id="wamid-3"))
    assert specs[0]["type"] == "interactive"
    buttons = specs[0]["buttons"]
    assert [b["title"] for b in buttons] == ["Approve", "Reject"]
    pid = buttons[0]["id"].split(":", 1)[1]
    assert buttons[0]["id"].startswith("approve:")
    assert buttons[1]["id"].startswith("reject:")
    assert db.query(models.Proposal).count() == 1
    assert db.query(models.Order).count() == 0  # proposal only — no order before approval


# --------------------------------------------------------------------------- #
# Interactive approve/reject buttons
# --------------------------------------------------------------------------- #
def test_button_approve_executes_order_once(orch, db, fresh_env):
    process_webhook(db, text_message(PHONE_A, "find wireless earbuds under PKR 2,000"))
    specs = process_webhook(db, text_message(PHONE_A, "buy the first one", msg_id="wamid-3"))
    pid = specs[0]["buttons"][0]["id"].split(":", 1)[1]

    specs = process_webhook(db, button_message(PHONE_A, f"approve:{pid}"))
    assert "confirmed" in specs[0]["body"].lower()
    assert db.query(models.Order).count() == 1

    # tapping approve again is idempotent — still exactly one order
    specs = process_webhook(db, button_message(PHONE_A, f"approve:{pid}", msg_id="wamid-9"))
    assert db.query(models.Order).count() == 1
    assert fresh_env.place_order_calls == 1

    events = {e.event_type for e in db.query(models.AuditEvent).filter_by(user_id=PHONE_A).all()}
    assert "wa_button" in events
    assert "execution_succeeded" in events


def test_button_reject_cancels_proposal(orch, db, fresh_env):
    process_webhook(db, text_message(PHONE_A, "find wireless earbuds under PKR 2,000"))
    specs = process_webhook(db, text_message(PHONE_A, "buy the first one", msg_id="wamid-3"))
    pid = specs[0]["buttons"][0]["id"].split(":", 1)[1]

    specs = process_webhook(db, button_message(PHONE_A, f"reject:{pid}"))
    assert "rejected" in specs[0]["body"].lower()
    assert db.query(models.Order).count() == 0
    assert order_service.get_proposal_row(db, pid).status == "REJECTED"


def test_button_ownership_enforced(orch, db, fresh_env):
    # User A creates a proposal...
    process_webhook(db, text_message(PHONE_A, "find wireless earbuds under PKR 2,000"))
    specs = process_webhook(db, text_message(PHONE_A, "buy the first one", msg_id="wamid-3"))
    pid = specs[0]["buttons"][0]["id"].split(":", 1)[1]

    # ...user B tries to approve it from their own chat
    specs = process_webhook(db, button_message(PHONE_B, f"approve:{pid}"))
    assert "different chat" in specs[0]["body"].lower()
    assert db.query(models.Order).count() == 0
    assert order_service.get_proposal_row(db, pid).status == "PENDING_APPROVAL"


def test_unknown_button_is_handled(orch, db, fresh_env):
    specs = process_webhook(db, button_message(PHONE_A, "explode:now"))
    assert "don't recognise" in specs[0]["body"].lower()


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
def test_statuses_payload_is_ignored(orch, db, fresh_env):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "e1", "changes": [{"field": "messages", "value": {"statuses": [{"id": "wamid-x", "status": "delivered"}]}}]}
        ],
    }
    assert process_webhook(db, payload) == []


def test_unsupported_message_type_gets_polite_reply(orch, db, fresh_env):
    payload = text_message(PHONE_A, "hi")
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": PHONE_A,
        "id": "wamid-img",
        "type": "image",
        "image": {"id": "media-1"},
    }
    specs = process_webhook(db, payload)
    assert len(specs) == 1
    assert "text messages" in specs[0]["body"].lower()


def test_session_mapping_same_phone_same_user(orch, db, fresh_env):
    process_webhook(db, text_message(PHONE_A, "find wireless earbuds under PKR 2,000", msg_id="m1"))
    process_webhook(db, text_message(PHONE_A, "find headphones", msg_id="m2"))
    users = db.query(models.User).filter_by(id=PHONE_A).all()
    assert len(users) == 1
    # the second search replaced the first in the same session
    import backend.services.user_service as user_service

    session = user_service.load_session_data(db, PHONE_A)
    assert session["last_results"]


def test_different_phones_are_different_users(orch, db, fresh_env):
    process_webhook(db, text_message(PHONE_A, "hi", msg_id="m1"))
    process_webhook(db, text_message(PHONE_B, "hi", msg_id="m2"))
    assert db.query(models.User).count() == 2


# --------------------------------------------------------------------------- #
# HTTP endpoint end-to-end
# --------------------------------------------------------------------------- #
def test_webhook_endpoint_sends_via_client(db, fresh_env):
    with TestClient(app) as c:
        r = c.post("/webhooks/whatsapp", json=text_message(PHONE_A, "find earbuds under PKR 1,500"))
        assert r.status_code == 200
        assert r.json()["replies"] == 1
        sent = wa_mock().sent
        assert len(sent) == 1
        assert sent[0]["to"] == PHONE_A
        assert "PKR" in sent[0]["body"]
        events = {e.event_type for e in db.query(models.AuditEvent).all()}
        assert "wa_outbound" in events


def test_webhook_endpoint_enforces_signature_when_secret_configured(db, fresh_env, monkeypatch):
    from backend.config import Settings

    monkeypatch.setattr(
        "backend.api.webhooks.get_settings",
        lambda: Settings(whatsapp_app_secret="s3cret", whatsapp_verify_token="change-me"),
    )
    body = json.dumps(text_message(PHONE_A, "hi")).encode()
    with TestClient(app) as c:
        # no signature -> 403
        r = c.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json"})
        assert r.status_code == 403
        # bad signature -> 403
        r = c.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "x-hub-signature-256": "sha256=bad"},
        )
        assert r.status_code == 403
        # good signature -> 200
        good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        r = c.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "x-hub-signature-256": good},
        )
        assert r.status_code == 200


def test_cloud_client_unauthorized_until_configured():
    client = WhatsAppCloudClient()
    assert not client.is_authorized
    with pytest.raises(IntegrationUnavailableError):
        client.send_text(PHONE_A, "hello")
    with pytest.raises(IntegrationUnavailableError):
        client.send_interactive_buttons(PHONE_A, "hello", [{"id": "x", "title": "Y"}])
