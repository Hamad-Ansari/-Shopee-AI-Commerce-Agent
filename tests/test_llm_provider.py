"""Phase 3: LLM provider interface — structured intent extraction."""

import json

import pytest
from conftest import FakeOllamaClient

from backend import dependencies
from backend.agents.llm import OllamaProvider, RuleBasedProvider
from backend.schemas.intents import IntentType


def test_rulebased_provider_matches_the_rules():
    provider = RuleBasedProvider()
    intent = provider.extract_intent("Find wireless earbuds under PKR 2,000", {}, "buyer")
    assert intent.type == IntentType.SEARCH
    assert intent.params["query"] == "wireless earbuds"
    assert intent.params["max_price_pkr"] == 2000.0

    intent = provider.extract_intent("list a water bottle at PKR 500 with stock 20", {}, "seller")
    assert intent.type == IntentType.SELLER_LISTING
    assert intent.params["price_pkr"] == 500.0
    assert intent.params["stock"] == 20


def test_ollama_provider_parses_structured_intent():
    payload = json.dumps(
        {"type": "search", "mode": "buyer", "params": {"query": "wireless earbuds", "max_price_pkr": 2000}}
    )
    client = FakeOllamaClient([payload])
    provider = OllamaProvider(model="test-model", client=client)
    intent = provider.extract_intent("Find wireless earbuds under PKR 2,000", {}, "buyer")
    assert intent.type == IntentType.SEARCH
    assert intent.params["max_price_pkr"] == 2000.0
    # format="json" is requested for structured output
    assert client.calls[0]["format"] == "json"
    assert not provider.last_used_fallback


def test_ollama_provider_strips_markdown_fences():
    payload = "```json\n" + json.dumps({"type": "help", "mode": "buyer", "params": {}}) + "\n```"
    client = FakeOllamaClient([payload])
    provider = OllamaProvider(model="test-model", client=client)
    intent = provider.extract_intent("hi", {}, "buyer")
    assert intent.type == IntentType.HELP


def test_ollama_provider_retries_once_then_falls_back_to_rules():
    client = FakeOllamaClient(["this is not json", "still not json"])
    provider = OllamaProvider(model="test-model", client=client, fallback=RuleBasedProvider())
    intent = provider.extract_intent("Find wireless earbuds under PKR 2,000", {}, "buyer")
    assert intent.type == IntentType.SEARCH  # rule-based result
    assert intent.params["max_price_pkr"] == 2000.0
    assert len(client.calls) == 2  # one retry, then fallback
    assert provider.last_used_fallback


def test_ollama_provider_raises_without_fallback():
    # two errors: initial call + one retry; with no fallback the error propagates
    client = FakeOllamaClient([ConnectionError("connection refused"), ConnectionError("connection refused")])
    provider = OllamaProvider(model="test-model", client=client)
    with pytest.raises(ConnectionError):
        provider.extract_intent("hi", {}, "buyer")
    assert len(client.calls) == 2


def test_provider_selected_from_settings():
    # conftest sets SHOPEE_LLM_PROVIDER=rulebased
    provider = dependencies.get_llm_provider()
    assert isinstance(provider, RuleBasedProvider)
