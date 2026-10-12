from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOPEE_", env_file=".env", extra="ignore")

    app_name: str = "Shopee AI Commerce Agent"
    database_url: str = "sqlite:///./shopee_ai_agent.db"
    mock_catalog_path: str = "data/mock_products.json"
    default_spending_limit_pkr: float = 50000.0
    max_quantity_per_order: int = 10
    rate_limit_per_minute: int = 30
    # Phase 6 — deployment hardening
    cors_allow_origins: str = "http://localhost:8501,http://127.0.0.1:8501"  # comma-separated; never "*" in prod
    checkpointer_path: str = "./shopee_checkpoints.db"  # persistent LangGraph checkpointer (survives restarts)
    # "mock" (default, Phase 1 demo) or "official" (Phase 5 — real Shopee Open Platform v2;
    # unauthorized until the credentials below are configured)
    shopee_client: str = "mock"
    # Phase 5 — Shopee Open Platform credentials (secrets: env/secret storage only)
    shopee_partner_id: str = ""
    shopee_partner_key: str = ""
    shopee_shop_id: str = ""
    shopee_access_token: str = ""  # shop access token (valid 4h — refresh via /api/v2/auth/access_token/get)
    shopee_base_url: str = "https://partner.shopeemobile.com"  # sandbox: https://openplatform.sandbox.test-stable.shopee.sg
    shopee_category_id: str = ""  # Shopee category id for new listings (required for most categories)
    whatsapp_verify_token: str = "change-me"
    # Phase 4 — WhatsApp Business Cloud API
    whatsapp_client: str = "mock"  # mock | cloud
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""  # used to verify X-Hub-Signature-256 on inbound webhooks
    whatsapp_api_version: str = "v21.0"
    # Phase 3 — AI orchestration
    llm_provider: str = "ollama"  # ollama | rulebased
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 30.0
    # If the LLM is unreachable or returns invalid output, fall back to the
    # deterministic rule-based extractor (keeps the agent demoable, always safe).
    llm_fallback_to_rules: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
