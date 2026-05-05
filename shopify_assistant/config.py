from pathlib import Path

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

# Load .env from shopify_assistant folder (so it works when uvicorn is run from project root)
_env_path = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    """
    Central configuration for the Shopify assistant service.
    Values can be overridden via environment variables or a .env file.
    """

    # Service
    APP_NAME: str = "Verdant · EcoSoul Intelligence"
    APP_VERSION: str = "0.1.0"
    PORT: int = 8010  # API server port (avoid 8000 if another app uses it)

    # Local LLM server (e.g. Ollama-style HTTP API)
    LLM_BASE_URL: AnyHttpUrl = "http://localhost:11438"
    LLM_MODEL_NAME: str = "qwen2.5:32b-instruct"

    # ClickHouse connection
    CLICKHOUSE_HOST: str = "localhost"
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_USERNAME: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_DATABASE: str = "shopify_assistant"

    # Shopify (you will fill these with real values in deployment)
    SHOPIFY_STORE_DOMAIN: Optional[str] = None  # e.g. "https://www.ecosoulhome.com"
    SHOPIFY_STOREFRONT_API_TOKEN: Optional[str] = None
    # Cart token from browser cookie "cart" (or from GET /cart.js). Optional: set in .env for server-side testing; in production frontend sends cart_token in request body.
    SHOPIFY_CART_TOKEN: Optional[str] = None

    # API auth (for /api/v1/* routes)
    API_AUTH_USERNAME: str = "shopify_client_app"
    API_AUTH_PASSWORD: str = "change-me"
    API_AUTH_TOKEN_TTL_SECONDS: int = 3600

    # MongoDB (optional; used for signup/login user store)
    # Example: mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority
    MONGO_URL: Optional[str] = None
    DB_NAME: str = "shopify_assistant"

    # Stable token for automation scripts (inventory refresh trigger).
    # If set, scripts can call refresh with:
    # Authorization: Bearer <INVENTORY_REFRESH_SERVICE_TOKEN>
    INVENTORY_REFRESH_SERVICE_TOKEN: Optional[str] = None

    # Ignore unknown keys in .env (e.g. AZURE_*), so adding deployment vars
    # doesn't break app startup.
    model_config = SettingsConfigDict(
        env_file=str(_env_path) if _env_path.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

