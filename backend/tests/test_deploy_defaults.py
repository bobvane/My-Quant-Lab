"""A default written down twice is a default that will disagree with itself.

Three findings from the deployment audit of v1.6.8 live here:

* the database URL was concatenated in ``docker-compose.yml`` and then handed to
  alembic through its ``ConfigParser``, so a password containing ``@``, ``:`` or
  ``/`` made SQLAlchemy parse a different host, and a bare ``%`` raised
  ``ValueError: invalid interpolation syntax`` before a single connection was
  attempted (ADR-098);
* the retired ``quantlab-scheduler`` service's probe ran ``pgrep``, which the image
  never installed,
  so it exited 127 forever -- and ``docker compose up -d`` still returned 0
  (ADR-099). The four roles live in one container now, so the merged probe asks
  each runtime piece a question that piece can actually answer;
* ``.env.example``, ``docker-compose.yml`` and ``app/core/config.py`` each kept a
  copy of the same defaults, and they had already drifted apart
  (``MARKET_DATA_PROVIDER``, ``NO_PROXY``,
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
ENTRYPOINT = DOCKER / "entrypoint.sh"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# `VAR=value` or `# VAR=value`: the example file documents optional knobs inside
# comments, and those are still the defaults an operator reads.
DOCUMENTED = re.compile(r"^(#?)\s*([A-Z][A-Z0-9_]*)=(\S*)", re.MULTILINE)
# `${VAR:-default}` and `${VAR:?message}` are how compose states a default and a
# requirement.
COMPOSE_DEFAULT = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}]*)\}")
COMPOSE_REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?([^}]*)\}")

# Defaults where the example and compose deliberately disagree. `.env.example` ships the
# real provider so a copied file works out of the box, while compose's own fallback stays
# offline so a bare `docker compose up` never reaches out; CI and the local stack override
# it back to `synthetic` in the shell, where a shell value wins (ADR-077, ADR-172).
# `MQL_VERSION` is no longer an exception: the template is not a product version mirror and
# ships the very `latest` compose falls back to (ADR-172).
COMPOSE_ONLY = {"MARKET_DATA_PROVIDER"}

# Defaults whose two layers mean different things on purpose.
CODE_EXCEPTIONS = {
    "APP_ENVIRONMENT": "a bare Settings() is for local work; the deployment ships production",
    "MARKET_DATA_PROVIDER": "a bare Settings() stays offline; the example ships the real provider",
    "SECRET_KEY": "both values are published placeholders production refuses, by name",
}

# Deployment knobs whose code default must be the documented one.
KNOBS = {
    "RATE_LIMIT_PER_MINUTE": "rate_limit_per_minute",
    "AI_DAILY_BUDGET_USD": "ai_daily_budget_usd",
    "AI_TASK_BUDGET_USD": "ai_task_budget_usd",
    "AI_DAILY_TASK_LIMIT": "ai_daily_task_limit",
    "AI_TASK_TIMEOUT_SECONDS": "ai_task_timeout_seconds",
    "LOG_LEVEL": "log_level",
    "DEFAULT_CURRENCY": "default_currency",
    "DEFAULT_TIMEZONE": "default_timezone",
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
    shared = _compose()["x-app-env"]
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


def test_the_app_probe_can_actually_run_where_it_is_declared() -> None:
    """ADR-099's lesson, in the merged container: the probe must ask answerable questions."""

    probe = _compose()["services"]["quantlab-app"]["healthcheck"]["test"]
    command = " ".join(probe) if isinstance(probe, list) else str(probe)
    assert "curl" in command, "the probe no longer asks the two HTTP doors for an answer"
    assert "inspect ping" in command, (
        "the probe no longer proves the celery worker is alive, so a container whose "
        "worker died would still report healthy"
    )
    assert "celerybeat-schedule" in command, (
        "the probe no longer checks that beat is scheduling, so a scheduler child that "
        "died is invisible until the next task is missed"
    )
    assert "pgrep" not in command, (
        "nothing in this container's state needs pgrep: that probe asked a question "
        "the image could not answer and exited 127 forever (ADR-099)"
    )
    image = _text(DOCKER / "Dockerfile.app")
    install = [line for line in image.splitlines() if "apt-get install" in line]
    assert install, "the app image no longer installs anything"
    assert "curl" in install[0], (
        "the image runs a curl probe without installing curl: it exits 127 forever"
    )
    if "ps aux" in _text(CI):
        assert "procps" in install[0], (
            "CI dumps processes with `ps aux` inside the container, which needs procps"
        )


def test_migrations_finish_before_the_children_start() -> None:
    """The merged App migrates once, in the launcher, before any child exists.

    Two containers used to encode this ordering as `depends_on: service_healthy`;
    with one container the ordering is code, so this guard reads the code -- and
    holds the second half too: no child migrates on its own (ADR-099).
    """

    text = _text(ENTRYPOINT)
    main = text[text.index("main_app() {") : text.index('case "$ROLE" in')]
    assert "run_migrations || exit 1" in main, "the launcher no longer migrates at all"
    assert main.index("run_migrations") < main.index("start beat"), (
        "a child may start before the schema it reads is up to date"
    )
    children = main[main.index("start beat") : main.index("wait -n")]
    assert "alembic" not in children, (
        "a child runs migrations of its own: two migrators in one App race on the "
        "same version table"
    )
    compose = _text(COMPOSE)
    assert not re.search(r"\balembic\s+upgrade\b", compose), (
        "compose runs alembic outside the launcher; migration is the App's job, once"
    )
