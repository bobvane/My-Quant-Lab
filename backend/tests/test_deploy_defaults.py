"""A default written down twice is a default that will disagree with itself.

Three findings from the deployment audit of v1.6.8 live here:

* the database URL was concatenated in ``docker-compose.yml`` and then handed to
  alembic through its ``ConfigParser``, so a password containing ``@``, ``:`` or
  ``/`` made SQLAlchemy parse a different host, and a bare ``%`` raised
  ``ValueError: invalid interpolation syntax`` before a single connection was
  attempted (ADR-098);
* ``quantlab-scheduler``'s probe ran ``pgrep``, which the image never installed,
  so it exited 127 forever -- and ``docker compose up -d`` still returned 0
  (ADR-099);
* ``.env.example``, ``docker-compose.yml`` and ``app/core/config.py`` each kept a
  copy of the same defaults, and they had already drifted apart
  (``MARKET_DATA_PROVIDER``, ``DOCKER_PROXY_URL``, ``NO_PROXY``,
  ``CORS_ORIGINS``), while the image declared probes that compose always
  overrode (ADR-100).

The guards below hold the fixes in place: one place assembles the URL, one place
declares a probe, and every default the deployment ships is the default the
example documents.
"""

from __future__ import annotations

import configparser
import pathlib
import re

import pytest
import yaml
from sqlalchemy.engine import make_url

from app.core.config import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
BACKEND = REPO_ROOT / "backend"
DOCKER = REPO_ROOT / "docker"

# `VAR=value` or `# VAR=value`: the example file documents optional knobs inside
# comments, and those are still the defaults an operator reads.
DOCUMENTED = re.compile(r"^(#?)\s*([A-Z][A-Z0-9_]*)=(\S*)", re.MULTILINE)
# `${VAR:-default}` and `${VAR:?message}` are how compose states a default and a
# requirement.
COMPOSE_DEFAULT = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}]*)\}")
COMPOSE_REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?([^}]*)\}")

# The example file pins the release it ships (scripts/version.sh rewrites it), so
# it deliberately disagrees with compose's fallback: `latest` follows the project
# when no .env exists at all.
COMPOSE_ONLY = {"MQL_VERSION"}

# Defaults whose two layers mean different things on purpose.
CODE_EXCEPTIONS = {
    "APP_ENVIRONMENT": "a bare Settings() is for local work; the deployment ships production",
    "DOCKER_PROXY_URL": "None means 'no proxy configured'; the example documents the bundled one",
    "SECRET_KEY": "both values are published placeholders production refuses, by name",
}

# Deployment knobs whose code default must be the documented one.
KNOBS = {
    "MARKET_DATA_PROVIDER": "market_data_provider",
    "RATE_LIMIT_PER_MINUTE": "rate_limit_per_minute",
    "AI_DAILY_BUDGET_USD": "ai_daily_budget_usd",
    "LOG_LEVEL": "log_level",
    "DEFAULT_CURRENCY": "default_currency",
    "DEFAULT_TIMEZONE": "default_timezone",
    "RESOURCE_COLLECTION_ENABLED": "resource_collection_enabled",
    "RESOURCE_RETENTION_RAW_DAYS": "resource_retention_raw_days",
    "RESOURCE_RETENTION_ROLLUP_DAYS": "resource_retention_rollup_days",
    "POSTGRES_USER": "postgres_user",
    "POSTGRES_DB": "postgres_db",
    "CORS_ORIGINS": "cors_origins",
}


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose() -> dict:
    return yaml.safe_load(_text(COMPOSE)) or {}


def _documented() -> dict[str, str]:
    """Every default `.env.example` states; an active line beats a comment."""

    documented: dict[str, str] = {}
    commented: dict[str, str] = {}
    for comment, name, value in DOCUMENTED.findall(_text(ENV_EXAMPLE)):
        (commented if comment else documented)[name] = value.strip()
    return {**commented, **documented}


def _literal(value: object) -> str:
    """The value compose ends up with, placeholders unwrapped."""

    text = str(value).strip()
    match = COMPOSE_DEFAULT.fullmatch(text)
    return match.group(2).strip() if match else text


def _compose_defaults(text: str) -> dict[str, str]:
    return {name: default.strip() for name, default in COMPOSE_DEFAULT.findall(text)}


def _code_default(field: str) -> str:
    declared = Settings.model_fields[field]
    value = declared.get_default(call_default_factory=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(sorted(str(item) for item in value))
    if value is None:
        return ""
    return str(value)


def _documented_origins(value: str) -> str:
    return ",".join(sorted(part.strip() for part in value.split(",") if part.strip()))


# --- one place assembles the database URL (ADR-098) -------------------------


def test_the_deployment_no_longer_hands_a_url_to_a_configparser() -> None:
    env = _text(BACKEND / "alembic" / "env.py")
    assert "set_main_option" not in env, "the URL must not be stored in the alembic config"
    assert "create_engine(settings.database_url" in env
    # Read as text: reading it through Config() depends on the machine's locale
    # encoding, which is how an em dash in a comment broke the suite once.
    ini = _text(BACKEND / "alembic.ini")
    assert re.search(r"^\s*sqlalchemy\.url\s*=", ini, re.MULTILINE) is None, (
        "alembic.ini kept a second copy of the URL"
    )
    assert ini.isascii(), "alembic reads this file with the locale encoding"


def test_the_configparser_would_have_refused_a_typical_password() -> None:
    """The mechanism, so the ban above does not look arbitrary."""

    parser = configparser.ConfigParser()
    with pytest.raises(ValueError, match="interpolation"):
        parser.set("alembic", "sqlalchemy.url", "postgresql+psycopg://u:pa%ss@db:5432/name")


@pytest.mark.parametrize("password", ["pa@ss", "pa:ss", "pa/ss", "pa%ss", "p%25ss@x/y:z", "plain"])
def test_every_awkward_password_survives_the_round_trip(password: str) -> None:
    settings = Settings(_env_file=None, database_url="", postgres_password=password)
    url = make_url(settings.database_url)
    assert url.password == password, "the password that reaches the driver is not the one given"
    assert url.host == "quantlab-postgres"
    assert url.port == 5432
    assert url.database == "quantlab"
    assert url.username == "quantlab"


def test_the_database_parts_compose_passes_are_the_parts_the_app_expects() -> None:
    shared = _compose()["x-backend-env"]
    assert "DATABASE_URL" not in shared, "compose is concatenating a URL again"
    assert _literal(shared["POSTGRES_HOST"]) == _code_default("postgres_host")
    assert _literal(shared["POSTGRES_PORT"]) == _code_default("postgres_port")
    assert _literal(shared["POSTGRES_USER"]) == _code_default("postgres_user")
    assert _literal(shared["POSTGRES_DB"]) == _code_default("postgres_db")


# --- one place declares each default (ADR-100) ------------------------------


def test_every_default_compose_writes_is_the_one_the_example_documents() -> None:
    documented = _documented()
    wrong = []
    for name, default in _compose_defaults(_text(COMPOSE)).items():
        if name in COMPOSE_ONLY:
            continue
        if name not in documented:
            wrong.append(f"{name} is set by compose but never documented in .env.example")
        elif documented[name] != default:
            wrong.append(
                f"{name}: compose says {default!r}, .env.example says {documented[name]!r}"
            )
    assert wrong == [], "the same default has two answers:\n" + "\n".join(wrong)


def test_every_required_variable_is_documented() -> None:
    documented = _documented()
    for name, message in COMPOSE_REQUIRED.findall(_text(COMPOSE)):
        assert name in documented, f"compose requires {name} ({message}) but never asks for it"


def test_the_code_defaults_are_the_ones_the_example_documents() -> None:
    documented = _documented()
    wrong = []
    for name, field in KNOBS.items():
        expected = (
            _documented_origins(documented[name]) if name == "CORS_ORIGINS" else documented[name]
        )
        actual = _code_default(field)
        if name == "CORS_ORIGINS":
            actual = _documented_origins(actual)
        if actual != expected:
            wrong.append(f"{name}: code says {actual!r}, .env.example says {expected!r}")
    assert wrong == [], "the code and the deployment disagree:\n" + "\n".join(wrong)
    assert set(CODE_EXCEPTIONS) <= set(documented), (
        "an exception listed here must still be documented in .env.example"
    )


# --- one place declares a probe (ADR-099, ADR-100) --------------------------


def test_every_service_declares_the_probe_that_runs() -> None:
    services = _compose()["services"]
    missing = [name for name, service in services.items() if not (service or {}).get("healthcheck")]
    assert missing == [], f"{missing} would never report their state"
    shipped = [
        path.name
        for path in sorted(DOCKER.glob("Dockerfile.*"))
        if re.search(r"^HEALTHCHECK\b", _text(path), re.MULTILINE)
    ]
    assert shipped == [], (
        f"{shipped} declare a probe that compose overrides; a probe nobody runs is not one"
    )


def test_the_scheduler_probe_can_actually_run_where_it_is_declared() -> None:
    scheduler = _compose()["services"]["quantlab-scheduler"]["healthcheck"]["test"]
    command = " ".join(scheduler) if isinstance(scheduler, list) else str(scheduler)
    assert "pgrep" in command, "the scheduler probe no longer proves beat is running"
    image = _text(DOCKER / "Dockerfile.backend")
    install = [line for line in image.splitlines() if "apt-get install" in line]
    assert install, "the backend image no longer installs anything"
    assert "procps" in install[0], (
        "the image runs a pgrep probe without installing procps: it exits 127 forever"
    )


def test_migrations_finish_before_the_queues_start() -> None:
    services = _compose()["services"]
    for name in ("quantlab-worker", "quantlab-scheduler"):
        condition = services[name]["depends_on"]["quantlab-api"]["condition"]
        assert condition == "service_healthy", (
            f"{name} may start before the api role has finished migrating"
        )


def test_the_proxy_runs_as_the_user_both_files_name() -> None:
    declared = re.search(r"^USER (\S+)", _text(DOCKER / "Dockerfile.proxy"), re.MULTILINE)
    assert declared, "the proxy image no longer states which user it runs as"
    assert declared.group(1) == _compose()["services"]["quantlab-docker-proxy"].get("user")
