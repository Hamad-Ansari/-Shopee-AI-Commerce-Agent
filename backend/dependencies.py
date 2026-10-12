"""Singletons shared by services, agents and the API layer (overridable in tests)."""

from backend.config import get_settings
from backend.integrations.shopee_client import MockShopeeClient, OfficialShopeeClient
from backend.services.policy_engine import PolicyEngine
from backend.services.product_service import ProductService

_client = None
_product_service = None
_policy_engine = None
_orchestrator = None


def get_shopee_client():
    global _client
    if _client is None:
        settings = get_settings()
        if settings.shopee_client == "official":
            _client = OfficialShopeeClient(
                partner_id=settings.shopee_partner_id,
                partner_key=settings.shopee_partner_key,
                shop_id=settings.shopee_shop_id,
                access_token=settings.shopee_access_token,
                base_url=settings.shopee_base_url,
                category_id=settings.shopee_category_id or None,
            )
        else:
            _client = MockShopeeClient()
    return _client


def set_shopee_client(client) -> None:
    global _client
    _client = client


def get_product_service() -> ProductService:
    global _product_service
    if _product_service is None:
        _product_service = ProductService()
    return _product_service


def get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine(get_settings())
    return _policy_engine


def reset_policy_engine() -> None:
    global _policy_engine
    _policy_engine = None


def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from backend.agents.graph import Orchestrator  # imported lazily to avoid cycles

        _orchestrator = Orchestrator()
    return _orchestrator


def set_orchestrator(orchestrator) -> None:
    """Override/reset the orchestrator singleton (tests)."""
    global _orchestrator
    _orchestrator = orchestrator


_llm_provider = None


def get_llm_provider():
    """Active LLM provider (Ollama by default, rule-based fallback wired in)."""
    global _llm_provider
    if _llm_provider is None:
        from backend.agents.llm import OllamaProvider, RuleBasedProvider

        settings = get_settings()
        if settings.llm_provider == "ollama":
            fallback = RuleBasedProvider() if settings.llm_fallback_to_rules else None
            _llm_provider = OllamaProvider(
                model=settings.ollama_model,
                base_url=settings.ollama_base_url,
                timeout=settings.ollama_timeout_seconds,
                fallback=fallback,
            )
        else:
            _llm_provider = RuleBasedProvider()
    return _llm_provider


def set_llm_provider(provider) -> None:
    global _llm_provider
    _llm_provider = provider


_whatsapp_client = None


def get_whatsapp_client():
    """Active WhatsApp client: MockWhatsAppClient by default, cloud when configured."""
    global _whatsapp_client
    if _whatsapp_client is None:
        from backend.integrations.whatsapp_client import MockWhatsAppClient, WhatsAppCloudClient

        settings = get_settings()
        if settings.whatsapp_client == "cloud":
            _whatsapp_client = WhatsAppCloudClient(
                access_token=settings.whatsapp_access_token,
                phone_number_id=settings.whatsapp_phone_number_id,
                api_version=settings.whatsapp_api_version,
            )
        else:
            _whatsapp_client = MockWhatsAppClient()
    return _whatsapp_client


def set_whatsapp_client(client) -> None:
    global _whatsapp_client
    _whatsapp_client = client
