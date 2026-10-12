"""WhatsApp Business Cloud API clients (Phase 4).

Same pattern as the marketplace clients: an interface with two implementations —
MockWhatsAppClient (default; records everything, observable in tests) and
WhatsAppCloudClient (real Meta Graph API; only authorised once an access token
and phone-number ID are configured).
"""

from abc import ABC, abstractmethod

import httpx

from backend.exceptions import IntegrationUnavailableError, MarketplaceError


class WhatsAppClient(ABC):
    is_authorized: bool = False

    @abstractmethod
    def send_text(self, to: str, body: str) -> dict:
        """Send a plain text message. Returns the provider message id."""

    @abstractmethod
    def send_interactive_buttons(self, to: str, body: str, buttons: list[dict]) -> dict:
        """Send a message with quick-reply buttons.

        buttons: [{"id": "approve:<proposal_id>", "title": "Approve"}, ...]
        """


class MockWhatsAppClient(WhatsAppClient):
    """Default Phase 4 client: records outbound messages instead of sending them."""

    is_authorized = True

    def __init__(self):
        self.sent: list[dict] = []

    def send_text(self, to: str, body: str) -> dict:
        self.sent.append({"to": to, "type": "text", "body": body})
        return {"id": f"WAMOCK-{len(self.sent):04d}"}

    def send_interactive_buttons(self, to: str, body: str, buttons: list[dict]) -> dict:
        self.sent.append({"to": to, "type": "interactive", "body": body, "buttons": buttons})
        return {"id": f"WAMOCK-{len(self.sent):04d}"}


class WhatsAppCloudClient(WhatsAppClient):
    """Real Meta WhatsApp Business Cloud API client.

    Stays unauthorised (and every send raises IntegrationUnavailableError) until an
    access token and phone-number ID are configured — set SHOPEE_WHATSAPP_CLIENT=cloud
    plus the credentials, and outbound replies go live.
    """

    def __init__(self, access_token: str | None = None, phone_number_id: str | None = None, api_version: str = "v21.0"):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.is_authorized = bool(access_token and phone_number_id)

    def _post(self, payload: dict) -> dict:
        if not self.is_authorized:
            raise IntegrationUnavailableError(
                "WhatsApp Cloud API credentials are not configured. Outbound replies stay "
                "in the audit log until SHOPEE_WHATSAPP_ACCESS_TOKEN and "
                "SHOPEE_WHATSAPP_PHONE_NUMBER_ID are set."
            )
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MarketplaceError(f"WhatsApp API call failed: {e}") from e
        data = resp.json()
        return {"id": data.get("messages", [{}])[0].get("id")}

    def send_text(self, to: str, body: str) -> dict:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": body},
            }
        )

    def send_interactive_buttons(self, to: str, body: str, buttons: list[dict]) -> dict:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                            for b in buttons
                        ]
                    },
                },
            }
        )
