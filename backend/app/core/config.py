"""Core application configuration.

All configuration comes from environment variables so that the same image can be
used in every environment. Secrets are never logged and never returned by the API.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

from app import __version__ as package_version

# The values that ship in `.env.example` or in the setup instructions. `secret_key`
# is not just a signing seed: `app.infrastructure.secrets` derives the at-rest
# encryption key from it, so a deployment that keeps a published value hands
# anyone holding a copy of the database -- or of this repository -- the provider
# API keys stored in it (ADR-077).
#
# Two kinds of published value need two kinds of match. Markers are instruction
# words: any secret containing one was never generated. Published secrets are
# literals this repository handed out, and they are compared *exactly*, because a
# legitimate random secret may happen to start with the same hex digits -- the
# test suite's own secret did (ADR-077).
_SECRET_PLACEHOLDERS = (
    "change-me",
    "change_me",
    "changeme",
    "your-",
    "replace-me",
    "example",
    "placeholder",
    "insecure",
)
_PUBLISHED_SECRETS = (
    "change-me-openssl-rand-hex-32",
    "change-me-in-production",
    "0123456789abcdef0123456789abcdef",
)
_SECRET_MIN_LENGTH = 32


def _refused_secret_message(reason: str) -> str:
    return (
        f"SECRET_KEY {reason}; it is the key material for at-rest encryption, "
        "so a published value protects nothing. Generate one with "
        "`openssl rand -hex 32` and set SECRET_KEY in .env"
    )


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
    # `host` and `port` used to live here. Nothing in this repository ever read
    # them: the container binds `0.0.0.0` and the port the entrypoint passes to
    # uvicorn, and which address the port is published on is compose's `API_BIND`
    # (ADR-102). Declaring them made an operator's `HOST=`/`PORT=` look honoured.

    # The database URL is assembled from its parts, never concatenated (ADR-098).
    # A password pasted into a URL breaks it in two ways: `@`, `:` and `/` make
    # SQLAlchemy parse the wrong host, and a bare `%` used to make alembic's
    # ConfigParser raise before a single connection was attempted. The parts are
    # given to PostgreSQL (the `POSTGRES_*` variables) and to us, so the value is
    # encoded exactly once, here.
    postgres_user: str = "quantlab"
    postgres_password: str = "quantlab"
    postgres_db: str = "quantlab"
    postgres_host: str = "quantlab-postgres"
    postgres_port: int = 5432
    # An explicit DATABASE_URL still wins: tests and probe scripts point the app
    # at SQLite, and an operator who sets it means it.
    database_url: str = ""
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
    # process open; that process binds to 127.0.0.1 (API_BIND), but the bundled
    # web container publishes ${WEB_BIND:-0.0.0.0}:8081 and proxies /api to it
    # *with* the token injected, so the token stops clients that bypass the
    # container — not the LAN clients that use it (ADR-097). Close the
    # deployment where it is open: WEB_BIND=127.0.0.1, or a firewall in front.
    api_auth_token: str | None = None
    # Per-IP limit for mutating API requests (POST/PUT/DELETE). 0 disables it;
    # skipped entirely under APP_ENVIRONMENT=test. Read endpoints (GET) are not
    # limited so dashboards and polling keep working.
    rate_limit_per_minute: int = 60
    ghostfolio_base_url: str | None = None
    ghostfolio_api_key: str | None = None
    # The default is the provider that works with no network and no API key, and
    # it is the one `.env.example` and docker-compose ship: a default nobody
    # deploys is a second answer to the same question (ADR-100). Real market data
    # is an explicit choice, `MARKET_DATA_PROVIDER=yahoo_finance`.
    market_data_provider: str = "synthetic"
    # Symbols the scheduled sync keeps warm. Empty means "use whatever the
    # active provider declares", which avoids hard-coding DEMO-* tickers that
    # only exist for the synthetic provider.
    market_data_watchlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ai_daily_budget_usd: float = 2.0

    default_currency: str = "USD"
    default_timezone: str = "UTC"

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

    @model_validator(mode="after")
    def _assemble_the_database_url(self) -> Settings:
        """Build the URL from its parts unless the deployment gave us one.

        ``URL.create`` percent-encodes the password, so the value that reaches
        ``create_engine`` says exactly what the deployment said, whatever it
        contains (ADR-098).
        """

        if not self.database_url:
            self.database_url = URL.create(
                "postgresql+psycopg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                database=self.postgres_db,
            ).render_as_string(hide_password=False)
        return self

    @model_validator(mode="after")
    def _refuse_the_example_secret_in_production(self) -> Settings:
        """Refuse to serve production with a secret that is in the repository.

        The example values shipped in `.env.example` are public, and the at-rest
        key for stored provider credentials is derived from this value, so a
        deployment that keeps one is not protecting anything (ADR-077). Local
        development and test environments keep working unchanged.
        """

        if not self.is_production:
            return self
        secret = self.secret_key.strip()
        if not secret:
            raise ValueError(
                "SECRET_KEY is empty; generate one with `openssl rand -hex 32` and set it in .env"
            )
        lowered = secret.lower()
        for marker in _SECRET_PLACEHOLDERS:
            if marker in lowered:
                raise ValueError(
                    _refused_secret_message(
                        f"still holds an example value (it contains {marker!r})"
                    )
                )
        for published in _PUBLISHED_SECRETS:
            if lowered == published:
                raise ValueError(_refused_secret_message("is a value this repository published"))
        if len(secret) < _SECRET_MIN_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {_SECRET_MIN_LENGTH} characters in "
                "production; generate one with `openssl rand -hex 32` and set "
                "SECRET_KEY in .env"
            )
        return self

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
