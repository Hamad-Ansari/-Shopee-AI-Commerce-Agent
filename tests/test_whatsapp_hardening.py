"""Post-pack hardening: WhatsApp reliability.

- Meta webhook retry dedup: the same wamid delivered twice is processed once
  (at-most-once), audited as wa_duplicate, and never re-answered.
- Ack-200-then-send-async: the endpoint returns immediately; replies are sent by
  a background task (TestClient waits for background tasks, so sends are
  observable after the POST returns).
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend import dependencies
from backend.database import models
from backend.integrations.whatsapp_client import MockWhatsAppClient
from backend.main import app
from backend.services.whatsapp_service import process_webhook, purge_old_wa_messages

PHONE = "923001112222"


def text_message(phone, body, msg_id):
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


def button_message(phone, payload_id, msg_id):
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


def _wa_events(db, event_type):
    return db.query(models.AuditEvent).filter_by(event_type=event_type).all()


# --------------------------------------------------------------------------- #
# Dedup (Meta retries)
# --------------------------------------------------------------------------- #
def test_same_wamid_twice_is_processed_once(orch, db, fresh_env):
    payload = text_message(PHONE, "hi", msg_id="dup-1")
    first = process_webhook(db, payload)
    second = process_webhook(db, payload)  # Meta retry: identical payload

    assert len(first) == 1
    assert second == []  # skipped entirely
    assert len(_wa_events(db, "wa_duplicate")) == 1
    assert len(_wa_events(db, "wa_inbound")) == 1  # inbound audited once
    assert db.query(models.WaMessage).count() == 1


def test_different_wamids_are_both_processed(orch, db, fresh_env):
    assert len(process_webhook(db, text_message(PHONE, "hi", msg_id="m-1"))) == 1
    assert len(process_webhook(db, text_message(PHONE, "hello again", msg_id="m-2"))) == 1
    assert db.query(models.WaMessage).count() == 2
    assert db.query(models.User).filter_by(id=PHONE).count() == 1  # same user


def test_messages_without_id_are_never_deduped(orch, db, fresh_env):
    payload = text_message(PHONE, "hi", msg_id="no-id-1")
    del payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    assert len(process_webhook(db, payload)) == 1
    assert len(process_webhook(db, payload)) == 1  # no id -> no dedup -> answered again
    assert db.query(models.WaMessage).count() == 0


def test_statuses_payload_has_no_dedup_rows(orch, db, fresh_env):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "e1", "changes": [{"field": "messages", "value": {"statuses": [{"id": "s1", "status": "read"}]}}]}
        ],
    }
    assert process_webhook(db, payload) == []
    assert process_webhook(db, payload) == []  # receipts are idempotent by nature
    assert db.query(models.WaMessage).count() == 0


def test_duplicate_button_tap_is_skipped(orch, db, fresh_env):
    process_webhook(db, text_message(PHONE, "find wireless earbuds under PKR 2,000", msg_id="b-1"))
    specs = process_webhook(db, text_message(PHONE, "buy the first one", msg_id="b-2"))
    pid = specs[0]["buttons"][0]["id"].split(":", 1)[1]

    tap = button_message(PHONE, f"approve:{pid}", msg_id="b-3")
    assert len(process_webhook(db, tap)) == 1
    assert db.query(models.Order).count() == 1

    # Meta retries the exact same tap -> deduped before the executor is even reached
    assert process_webhook(db, tap) == []
    assert db.query(models.Order).count() == 1
    assert fresh_env.place_order_calls == 1
    assert len(_wa_events(db, "wa_duplicate")) == 1


def test_purge_old_wa_messages(db, fresh_env):
    old = models.WaMessage(message_id="old-1", user_id=PHONE, wa_type="text")
    old.created_at = datetime.utcnow() - timedelta(days=10)
    db.add(old)
    db.add(models.WaMessage(message_id="new-1", user_id=PHONE, wa_type="text"))
    db.commit()

    deleted = purge_old_wa_messages(db, days=7)
    assert deleted == 1
    remaining = {m.message_id for m in db.query(models.WaMessage).all()}
    assert remaining == {"new-1"}


# --------------------------------------------------------------------------- #
# Endpoint: ack 200, send async (background task), end-to-end
# --------------------------------------------------------------------------- #
def test_endpoint_dedups_retries_and_sends_once(db, fresh_env):
    with TestClient(app) as c:
        payload = text_message(PHONE, "find earbuds under PKR 1,500", msg_id="ep-1")
        r1 = c.post("/webhooks/whatsapp", json=payload)
        r2 = c.post("/webhooks/whatsapp", json=payload)  # retry
        assert r1.status_code == 200 and r1.json() == {"status": "ok", "replies": 1}
        assert r2.status_code == 200 and r2.json() == {"status": "ok", "replies": 0}

    # background task ran (TestClient waits for it): exactly one reply was sent
    sent = wa_mock().sent
    assert len(sent) == 1
    assert sent[0]["to"] == PHONE
    assert db.query(models.WaMessage).count() == 1
    assert len(_wa_events(db, "wa_duplicate")) == 1
    assert len(_wa_events(db, "wa_outbound")) == 1  # audited by the background task


def test_endpoint_full_flow_with_dedup(db, fresh_env):
    with TestClient(app) as c:
        c.post("/webhooks/whatsapp", json=text_message(PHONE, "find wireless earbuds under PKR 2,000", msg_id="f-1"))
        r = c.post("/webhooks/whatsapp", json=text_message(PHONE, "buy the first one", msg_id="f-2"))
        assert r.json()["replies"] == 1

        sent = wa_mock().sent
        interactive = [s for s in sent if s["type"] == "interactive"]
        assert len(interactive) == 1
        pid = interactive[0]["buttons"][0]["id"].split(":", 1)[1]

        r = c.post("/webhooks/whatsapp", json=button_message(PHONE, f"approve:{pid}", msg_id="f-3"))
        assert r.json()["replies"] == 1
        assert db.query(models.Order).count() == 1

        # Meta retries both the buy and the tap -> nothing happens again
        c.post("/webhooks/whatsapp", json=text_message(PHONE, "buy the first one", msg_id="f-2"))
        c.post("/webhooks/whatsapp", json=button_message(PHONE, f"approve:{pid}", msg_id="f-3"))
        assert db.query(models.Order).count() == 1
        assert fresh_env.place_order_calls == 1
        assert len(_wa_events(db, "wa_duplicate")) == 2
        # exactly 3 replies total: search, interactive buy, confirmation
        assert len(wa_mock().sent) == 3


def test_wa_messages_table_is_registered():
    assert "wa_messages" in models.Base.metadata.tables
    cols = {c.name for c in models.Base.metadata.tables["wa_messages"].columns}
    assert cols == {"message_id", "user_id", "wa_type", "created_at"}
