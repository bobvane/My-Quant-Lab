"""A setting nobody reads is a promise nobody keeps (ADR-084).

Four settings used to live in ``app/core/config.py``, were documented in
``.env.example`` and were handed to the containers by ``docker-compose.yml`` --
and no code in this repository ever read them. ``scan_cron`` described itself as
"the Celery beat crontab used by the signal scanner" while ``celery_app.py``
hard-codes that schedule; the three AI ones described a provider that is
configured in the database instead. An operator who sets a knob and sees nothing
change cannot tell that from a bug, so the fix is deletion -- and this guard
keeps the class from coming back.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "backend" / "app" / "core" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: Directories that could consume a setting or a documented environment
#: variable. ``backend/tests`` is deliberately not one of them: a setting only a
#: test reads is still unread by the product.
CONSUMER_ROOTS = (
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "scripts",
    REPO_ROOT / "docker",
    REPO_ROOT / ".github",
    REPO_ROOT / "frontend" / "src",
)
CONSUMER_FILES = (REPO_ROOT / "docker-compose.yml", REPO_ROOT / "docker-compose.build.yml")
SUFFIXES = {".py", ".sh", ".ps1", ".yml", ".yaml", ".ts", ".vue", ".conf", ".js"}
SKIP_PARTS = {".venv", "node_modules", "dist", "__pycache__", ".git"}


def _settings_fields() -> list[str]:
    """The fields declared directly on the ``Settings`` class."""

    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return [
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            ]
    raise AssertionError("config.py declares no Settings class")


def _consumer_text() -> str:
    """Every file that could read a setting, as one blob of text."""

    chunks: list[str] = []
    for root in CONSUMER_ROOTS:
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix in SUFFIXES
                and not SKIP_PARTS.intersection(path.parts)
                and path != CONFIG
            ):
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    for path in CONSUMER_FILES:
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _validator_reads() -> str:
    """References a field makes to itself inside ``config.py``.

    A module-level validator is a real reader: ``_assemble_the_database_url``
    consumes the five ``postgres_*`` parts to build the URL that every other
    consumer reads. An exemption list would let a genuinely dead field hide
    behind a hand-written reason, so the guard looks for the reference instead.
    """

    return "\n".join(
        line for line in CONFIG.read_text(encoding="utf-8").splitlines() if "self." in line
    )


def test_every_setting_is_read_somewhere() -> None:
    """``settings.host`` is not the same claim as ``request.client.host``.

    This guard used to look for a bare ``.name`` anywhere in the consumers, so
    ``host`` and ``port`` passed it on the strength of ``request.client.host``,
    ``parsed.port`` and other unrelated objects -- while no code read either
    setting (ADR-102). A read now has to name the settings object, and a field a
    validator consumes has to be named by that validator.
    """

    consumer = _consumer_text()
    validator = _validator_reads()
    unread = sorted(
        name
        for name in _settings_fields()
        if not (
            re.search(rf"settings\.{re.escape(name)}\b", consumer)
            or re.search(rf"self\.{re.escape(name)}\b", validator)
        )
    )
    assert unread == [], f"settings nobody reads: {unread} -- honour them or delete them (ADR-084)"


def test_every_documented_env_key_is_consumed() -> None:
    """``.env.example`` must not document a variable that goes nowhere."""

    keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE)
    text = _consumer_text()
    orphans = sorted(key for key in keys if key not in text)
    assert orphans == [], f"documented but never consumed: {orphans} (ADR-084)"


def test_the_knobs_that_did_nothing_are_gone() -> None:
    """The six that were deleted: keeping one is what this ADR forbids."""

    config = CONFIG.read_text(encoding="utf-8")
    for name in ("ai_provider_base_url", "ai_provider_api_key", "ai_default_model", "scan_cron"):
        assert name not in config, f"{name} is back; it had no reader (ADR-084)"

    # The two the deployment owns: compose publishes the port and the entrypoint
    # passes it to uvicorn, so a `Settings.port` only made `PORT=` look honoured.
    for name in ("host", "port"):
        assert not re.search(rf"^\s+{name}:\s", config, re.MULTILINE), (
            f"Settings.{name} is back; the deployment owns it (ADR-102)"
        )

    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in ("AI_PROVIDER_BASE_URL", "AI_PROVIDER_API_KEY", "AI_DEFAULT_MODEL"):
        assert key not in env, f"{key} promises a provider entry point that does not exist"


def test_the_withdrawn_tunnel_kept_no_settings() -> None:
    """ADR-125 was withdrawn in v2.6.0, and its five knobs went with it.

    The tunnel left the runtime entirely (no manager, no router, no `cloudflared` in
    the image), so a `temporary_access_*` field here would be a knob whose reader no
    longer exists -- exactly the class this ADR is about.
    """

    config = CONFIG.read_text(encoding="utf-8")
    for name in (
        "temporary_access_enabled",
        "temporary_access_target_url",
        "temporary_access_cloudflared_bin",
        "temporary_access_max_duration_seconds",
        "temporary_access_start_timeout_seconds",
    ):
        assert not re.search(rf"^\s+{name}:\s", config, re.MULTILINE), (
            f"Settings.{name} is back; the public tunnel it configured was withdrawn (ADR-125)"
        )
