"""Core application configuration.

All configuration comes from environment variables so that the same image can be
used in every environment. Secrets are never logged and never returned by the API.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "My Quant Lab"
    app_version: str = "0.0.1"
    environment: str = Field(default="development")

    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8080

    database_url: str = Field(
        default="postgresql+psycopg://quantlab:quantlab@quantlab-postgres:5432/quantlab"
    )
    database_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_connect_timeout: int = Field(
        default=10,
        ge=1,
        le=120,
        description="Seconds to wait for a PostgreSQL connection before giving up.",
    )

    redis_url: str = Field(default="redis://quantlab-redis:6379/0")
    celery_broker_url: str = Field(default="redis://quantlab-redis:6379/1")
    celery_result_backend: str = Field(default="redis://quantlab-redis:6379/2")

    # `NoDecode` is essential: without it pydantic-settings tries to json.loads()
    # the raw environment variable, and a comma separated value such as
    # "http://a:1,http://b:2" raises SettingsError before our validator runs.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:8081"]
    )

    # Secrets are write-only. They are stored encrypted at rest by
    # `app.infrastructure.secrets` and are never echoed back by the API.
    secret_key: str = Field(default="change-me-in-production")
    ghostfolio_base_url: str | None = None
    ghostfolio_api_key: str | None = None
    market_data_provider: str = "yahoo_finance"
    ai_provider_base_url: str | None = None
    ai_provider_api_key: str | None = None
    ai_default_model: str | None = None
    ai_daily_budget_usd: float = 2.0

    default_currency: str = "USD"
    default_timezone: str = "UTC"

    scan_cron: str = Field(
        default="*/15 * * * *",
        description="Celery beat crontab used by the signal scanner.",
    )

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma separated list or a JSON array from the environment."""

        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()


settings = get_settings()
