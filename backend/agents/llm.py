"""LLM provider interface (Phase 3).

The orchestrator only depends on the LLMProvider interface. Ollama is the default
implementation (local, free, swappable for any hosted LLM by config); a
deterministic rule-based provider is always available as a fallback so the agent
keeps working when no model server is reachable.

Structured output: the provider returns a backend.schemas.intents.Intent — the same
Pydantic model the rule-based extractor produces — so downstream routing, tools and
tests are provider-agnostic.
"""

import json
from abc import ABC, abstractmethod

from backend.agents.intents import extract_intent as extract_intent_rules
from backend.agents.prompts import EXTRACTION_SYSTEM_PROMPT
from backend.schemas.intents import Intent


class LLMProvider(ABC):
    name: str = "abstract"
    last_used_fallback: bool = False

    @abstractmethod
    def extract_intent(self, message: str, session: dict, role: str) -> Intent:
        """Extract a structured Intent from one user message."""


class RuleBasedProvider(LLMProvider):
    """Deterministic extractor — the Phase 1 behaviour, kept as the safe fallback."""

    name = "rulebased"

    def extract_intent(self, message: str, session: dict, role: str) -> Intent:
        self.last_used_fallback = False
        return extract_intent_rules(message, session, role)


def build_extraction_messages(message: str, session: dict, role: str) -> list[dict]:
    schema = json.dumps(Intent.model_json_schema(), indent=2)
    pending = session.get("pending_proposal_id")
    context = {
        "message": message,
        "role": role,
        "session": {
            "pending_proposal_id": pending,
            "has_last_results": bool(session.get("last_results")),
            "last_results_count": len(session.get("last_results") or []),
        },
    }
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT + "\n\nIntent JSON schema:\n" + schema},
        {"role": "user", "content": json.dumps(context)},
        {"role": "user", "content": "Return ONLY the Intent JSON object."},
    ]


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class OllamaProvider(LLMProvider):
    """Ollama (default) — structured JSON intent extraction via a local model.

    Uses format="json" plus Pydantic validation, with one retry on invalid output.
    If the model server is unreachable or keeps failing, falls back to the
    rule-based provider (when enabled) and sets last_used_fallback.
    """

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        client=None,
        fallback: LLMProvider | None = None,
    ):
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._fallback = fallback
        self._client = client  # lazily created so importing this module never needs ollama

    def _get_client(self):
        if self._client is None:
            import ollama  # imported lazily; only needed when Ollama is the active provider

            self._client = ollama.Client(host=self.base_url, timeout=self.timeout)
        return self._client

    @staticmethod
    def _content_of(resp) -> str:
        message = resp.get("message") if isinstance(resp, dict) else resp.message
        content = message.get("content") if isinstance(message, dict) else message.content
        return content or ""

    def extract_intent(self, message: str, session: dict, role: str) -> Intent:
        self.last_used_fallback = False
        messages = build_extraction_messages(message, session, role)
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                resp = self._get_client().chat(
                    model=self.model,
                    messages=messages,
                    format="json",
                    options={"temperature": 0},
                )
                content = _strip_fences(self._content_of(resp))
                return Intent.model_validate_json(content)
            except Exception as e:  # noqa: BLE001 — any failure (network, JSON, schema) retries once
                last_error = e
                messages = messages + [
                    {
                        "role": "user",
                        "content": (
                            "Your previous output was not valid JSON for the Intent schema "
                            f"({e}). Return ONLY a valid Intent JSON object."
                        ),
                    }
                ]
        if self._fallback is not None:
            self.last_used_fallback = True
            return self._fallback.extract_intent(message, session, role)
        raise last_error if last_error else RuntimeError("LLM extraction failed")
