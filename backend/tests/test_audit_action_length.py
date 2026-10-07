"""An audit action must fit the column it is written to (ADR-186).

Background: ``audit_logs.action`` was created as ``VARCHAR(32)`` in ``0001_initial_schema``,
while ``POST /experiments/from-backtest/{run_id}`` writes the 41-character action
``strategy_experiment_adopted_from_backtest``. PostgreSQL rejects that write

    psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)

so on the deployed NAS *every* run that had not been adopted yet answered HTTP 500, while
the local suite -- SQLite, which accepts the same string happily -- stayed green. That is the
same shape as ADR-064 (revision ids longer than Alembic's own ``VARCHAR(32)``), and it is
answered the same way: read the widths themselves instead of trusting a green SQLite run.

Three rules are enforced here:

* the model must keep ``action`` 64 characters wide or wider;
* every ``action=`` string literal in ``app/`` must fit it -- and the scan must really see
  the long ones, so a scan that silently matched nothing cannot pass;
* the real migration chain must produce that width too, so the model cannot drift away from
  what a deployed database was built with.

The scan can only read literals. The two call sites that build ``action`` at runtime
(``strategies/lifecycle`` and ``ai/confirmation``) are therefore imported and measured
directly; ``notifications/service.py`` picks its short ``notify*`` strings inline.
"""

from __future__ import annotations

import ast
import contextlib
import logging
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.domain.models import AuditLog

APP_DIR = Path(__file__).resolve().parents[1] / "app"
ADOPTED_ACTION = "strategy_experiment_adopted_from_backtest"


def _column_width() -> int:
    """The ``audit_logs.action`` width as the model declares it."""

    width = AuditLog.__table__.c.action.type.length
    assert width is not None, "audit_logs.action must declare a length"
    return int(width)


def _action_literals() -> dict[str, list[str]]:
    """Every ``action="..."`` string literal in ``app/``, keyed by ``file:line``."""

    found: dict[str, list[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "action" or not isinstance(keyword.value, ast.Constant):
                    continue
                value = keyword.value.value
                if isinstance(value, str):
                    where = f"{path.relative_to(APP_DIR.parent)}:{node.lineno}"
                    found.setdefault(where, []).append(value)
    return found


def test_the_action_column_is_at_least_64_characters() -> None:
    """The width is the contract; 32 is what broke production."""

    width = _column_width()
    assert width >= 64, f"audit_logs.action is {width} characters wide; ADR-186 needs >= 64"


def test_every_audit_action_literal_fits_the_column() -> None:
    found = _action_literals()
    assert len(found) >= 8, f"this scan went blind, it only found {sorted(found)}"

    values = [value for values in found.values() for value in values]
    too_long = {
        where: value
        for where, literals in found.items()
        for value in literals
        if len(value) > _column_width()
    }
    assert not too_long, (
        "these audit actions cannot be stored in audit_logs.action on PostgreSQL; widen the "
        f"column or shorten the action (ADR-186): {too_long}"
    )

    assert ADOPTED_ACTION in values, (
        "the adoption action must be visible to this scan, otherwise the guard proves nothing"
    )
    assert len(ADOPTED_ACTION) == 41


def test_the_runtime_action_tables_fit_the_column() -> None:
    """Two call sites build ``action=`` at runtime, so import those tables and measure."""

    from app.ai.confirmation import ACTIONS as CONFIRMATION_ACTIONS
    from app.strategies.lifecycle import LIFECYCLE_ACTIONS

    runtime_actions = (*LIFECYCLE_ACTIONS, *CONFIRMATION_ACTIONS.values())
    assert runtime_actions, "no runtime actions found; was a table renamed?"
    too_long = [value for value in runtime_actions if len(value) > _column_width()]
    assert not too_long, f"runtime audit actions do not fit audit_logs.action: {too_long}"


@contextlib.contextmanager
def _logging_state_restored():
    """Put logging back the way alembic's ``fileConfig`` found it.

    ``backend/alembic/env.py:21-22`` calls ``fileConfig(config.config_file_name)``, which
    re-applies ``alembic.ini`` and, by default, disables every logger that already exists.
    Running the chain inside the suite therefore switched logging *off* for whatever ran
    later: ``tests/test_logging_redact.py`` and ``tests/test_error_incident.py`` capture
    records and saw none. ``tests/test_migrations_sqlite.py`` carries the same hazard but
    sorts after both files, so it never showed. Snapshot, run the chain, put it back.
    """

    root = logging.getLogger()
    saved_root = (list(root.handlers), root.level)
    saved_loggers = [
        (logger, logger.disabled, list(logger.handlers), logger.level, logger.propagate)
        for logger in list(logging.Logger.manager.loggerDict.values())
        if isinstance(logger, logging.Logger)
    ]
    try:
        yield
    finally:
        for logger, disabled, handlers, level, propagate in saved_loggers:
            logger.disabled = disabled
            logger.handlers = list(handlers)
            logger.level = level
            logger.propagate = propagate
        root.handlers = list(saved_root[0])
        root.level = saved_root[1]


@pytest.fixture(scope="module")
def migrated_sqlite_url(tmp_path_factory):
    """Run the real alembic chain against a scratch SQLite file."""

    from app.core.config import settings

    db_path = tmp_path_factory.mktemp("audit-width") / "app.sqlite3"
    url = f"sqlite+pysqlite:///{db_path}"

    original_url = settings.database_url
    settings.database_url = url  # type: ignore[assignment]
    try:
        from alembic.config import Config

        from alembic import command

        with _logging_state_restored():
            command.upgrade(Config("alembic.ini"), "head")
        yield url
    finally:
        settings.database_url = original_url  # type: ignore[assignment]


def test_the_migration_chain_declares_the_same_width(migrated_sqlite_url: str) -> None:
    """A fresh database built from the chain must agree with the model (ADR-186)."""

    engine = create_engine(migrated_sqlite_url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name, type FROM pragma_table_info('audit_logs') WHERE name = 'action'")
            ).all()
    finally:
        engine.dispose()

    assert rows, "audit_logs.action is missing from the migrated schema"
    declared = str(rows[0][1])
    assert str(_column_width()) in declared, (
        f"the migration chain declares audit_logs.action as {declared!r} while the model says "
        f"String({_column_width()}); revision 0020_audit_log_action_width must widen it"
    )
