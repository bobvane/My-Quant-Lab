"""Core application configuration.

All configuration comes from environment variables so that the same image can be
used in every environment. Secrets are never logged and never returned by the API.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app import __version__ as package_version


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "My Quant Lab"
    # Single source of truth: the package version, which `scripts/version.sh`
    # keeps in step with the released git tag. Hard-coding it here meant the API
    # reported 0.0.1 forever, so a running NAS could not be told apart by build.
    app_version: str = package_version.lstrip("v")
    # The deployment sets APP_ENVIRONMENT (see .env.example / compose). Without
    # an explicit alias pydantic-settings would only look at ENVIRONMENT, so
    # production detection silently stayed "development" and 500 responses
    # leaked exception details.
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENVIRONMENT", "ENVIRONMENT"),
    )

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
    # Optional bearer token for the REST API. Empty (default) leaves the API
    # open, which is safe only because it binds to 127.0.0.1 and is reached
    # through the web proxy. Set it before exposing the API on the LAN; the
    # bundled web container then injects the same token when proxying /api.
    api_auth_token: str | None = None
    # Per-IP limit for mutating API requests (POST/PUT/DELETE). 0 disables it;
    # skipped entirely under APP_ENVIRONMENT=test. Read endpoints (GET) are not
    # limited so dashboards and polling keep working.
    rate_limit_per_minute: int = 60
    ghostfolio_base_url: str | None = None
    ghostfolio_api_key: str | None = None
    market_data_provider: str = "yahoo_finance"
    # Symbols the scheduled sync keeps warm. Empty means "use whatever the
    # active provider declares", which avoids hard-coding DEMO-* tickers that
    # only exist for the synthetic provider.
    market_data_watchlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
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

    # --- Strategy lifecycle (Phase 8) -------------------------------------
    # Deterministic, evidence-gated promotion/degradation. Manual-only stages
    # (reference_signal, retired) are never applied automatically.
    lifecycle_auto_enabled: bool = True

    # --- System Resource Monitor ------------------------------------------
    # Phase 1a needs no Docker access at all: host metrics come from /proc via
    # psutil, and Quant Lab's own containers report through their cgroups.
    # Phase 1b adds a *read-only filtered proxy* so the collector may also see
    # every container on the NAS. The full Docker socket is never mounted into
    # the api/worker (see ADR-024).
    resource_collection_enabled: bool = True
    resource_retention_raw_days: int = Field(default=7, ge=1, le=90)
    resource_retention_rollup_days: int = Field(default=30, ge=1, le=365)
    # Comma-separated container paths to stat for disk usage; defaults to the
    # container's own filesystem (the NAS system disk via overlay).
    resource_disk_paths: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["/app"])
    # Read-only Docker stats proxy (phase 1b). Empty disables layer 2.
    docker_proxy_url: str | None = None
    docker_proxy_token: str | None = None
    # How the collector identifies Quant Lab containers among all NAS containers.
    quantlab_compose_project: str = "my-quant-lab"

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("api_auth_token", mode="before")
    @classmethod
    def _normalise_auth_token(cls, value: object) -> object:
        """Reject tokens whose characters would be unsafe in an HTTP header
        (and, for the bundled nginx proxy, unsafe inside an nginx config)."""

        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) < 8:
            raise ValueError("API_AUTH_TOKEN must be at least 8 characters")
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", text):
            raise ValueError("API_AUTH_TOKEN contains unsupported characters")
        return text

    @field_validator("resource_disk_paths", mode="before")
    @classmethod
    def _split_disk_paths(cls, value: object) -> object:
        return cls._split_origins(value)

    @field_validator("market_data_watchlist", mode="before")
    @classmethod
    def _split_watchlist(cls, value: object) -> object:
        """Accept a comma separated list or a JSON array, same as cors_origins."""

        return cls._split_origins(value)

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
