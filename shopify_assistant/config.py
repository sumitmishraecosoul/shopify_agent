from pathlib import Path

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings
from typing import Optional

# Load .env from shopify_assistant folder (so it works when uvicorn is run from project root)
_env_path = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    """
    Central configuration for the Shopify assistant service.
    Values can be overridden via environment variables or a .env file.
    """

    # Service
    APP_NAME: str = "Shopify Party Planning Assistant"
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
    SHOPIFY_STORE_DOMAIN: Optional[str] = None  # e.g. "yourstore.myshopify.com"
    SHOPIFY_STOREFRONT_API_TOKEN: Optional[str] = None

    class Config:
        env_file = str(_env_path) if _env_path.exists() else ".env"
        env_file_encoding = "utf-8"


settings = Settings()

