"""Database guards for the immutability invariants (ADR-094).

Two rules belong to the data itself, not to a service function:

* ``strategy_versions`` rows are immutable — ``dsl_json``, ``version`` and
  ``immutable_hash`` may never change; publish a new version instead.
* a completed ``backtest_results`` row is append-only — once ``result_hash`` is set,
  the summary/metrics/equity curve cannot be rewritten.

They were installed by the Alembic migration ``0002_immutability`` and repaired by
``0003_fix_triggers_json``, while every database built by ``Base.metadata.create_all()``
— which is what the unit suite, the probe scripts and any developer scratch database
uses — had no guard at all. That is how a trigger body that PostgreSQL rejects
(``operator does not exist: json = json``) reached production: the only database a
developer builds locally could not run the guard, so the guard was never exercised
before a tag shipped.

This module is the single definition of the guards. It is installed by
``Base.metadata``'s ``after_create`` event, so any database created from the models
gets them, and the Alembic chain calls the same function, so a migrated database gets
the same thing. It is dialect aware: PostgreSQL gets PL/pgSQL triggers (comparing the
``::text`` rendering of the json columns, because ``json`` has no equality operator),
SQLite gets the equivalent ``IS NOT`` triggers, which is the dialect the test suite runs
on.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event

from app.core.db import Base

__all__ = [
    "BACKTEST_RESULT_TRIGGER",
    "STRATEGY_VERSION_TRIGGER",
    "drop_immutability_triggers",
    "install_immutability_triggers",
    "statements_for",
]

STRATEGY_VERSION_TRIGGER = "trg_strategy_versions_immutable"
BACKTEST_RESULT_TRIGGER = "trg_backtest_results_immutable"

STRATEGY_VERSION_MESSAGE = "strategy_versions rows are immutable: create a new version instead"
BACKTEST_RESULT_MESSAGE = "completed backtest results are immutable"

# PostgreSQL. ``::text`` (not ``IS DISTINCT FROM`` on the json column) is the 0003 fix:
# the json type has no equality operator, so comparing it raises inside the trigger and
# every strategy-version UPDATE — including the routine is_current flip — returns 500.
_POSTGRES_DDL: tuple[str, ...] = (
    f"""
    CREATE OR REPLACE FUNCTION quantlab_reject_strategy_version_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF NEW.dsl_json::text IS DISTINCT FROM OLD.dsl_json::text
           OR NEW.version IS DISTINCT FROM OLD.version
           OR NEW.immutable_hash IS DISTINCT FROM OLD.immutable_hash THEN
            RAISE EXCEPTION '{STRATEGY_VERSION_MESSAGE}';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    f"DROP TRIGGER IF EXISTS {STRATEGY_VERSION_TRIGGER} ON strategy_versions;",
    f"""
    CREATE TRIGGER {STRATEGY_VERSION_TRIGGER}
    BEFORE UPDATE ON strategy_versions
    FOR EACH ROW
    EXECUTE FUNCTION quantlab_reject_strategy_version_mutation();
    """,
    f"""
    CREATE OR REPLACE FUNCTION quantlab_reject_result_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF OLD.result_hash IS NOT NULL
           AND (NEW.summary_json::text IS DISTINCT FROM OLD.summary_json::text
                OR NEW.metrics_json::text IS DISTINCT FROM OLD.metrics_json::text
                OR NEW.equity_curve_json::text IS DISTINCT FROM OLD.equity_curve_json::text
                OR NEW.result_hash IS DISTINCT FROM OLD.result_hash) THEN
            RAISE EXCEPTION '{BACKTEST_RESULT_MESSAGE}';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    f"DROP TRIGGER IF EXISTS {BACKTEST_RESULT_TRIGGER} ON backtest_results;",
    f"""
    CREATE TRIGGER {BACKTEST_RESULT_TRIGGER}
    BEFORE UPDATE ON backtest_results
    FOR EACH ROW
    EXECUTE FUNCTION quantlab_reject_result_mutation();
    """,
)

# SQLite stores JSON columns as text, so ``IS NOT`` compares the same rendering the
# PostgreSQL trigger compares — the dialects agree on what counts as a change.
_SQLITE_DDL: tuple[str, ...] = (
    f"""
    CREATE TRIGGER {STRATEGY_VERSION_TRIGGER}
    BEFORE UPDATE ON strategy_versions
    FOR EACH ROW WHEN OLD.dsl_json IS NOT NEW.dsl_json
        OR OLD.version IS NOT NEW.version
        OR OLD.immutable_hash IS NOT NEW.immutable_hash
    BEGIN
        SELECT RAISE(ABORT, '{STRATEGY_VERSION_MESSAGE}');
    END;
    """,
    f"""
    CREATE TRIGGER {BACKTEST_RESULT_TRIGGER}
    BEFORE UPDATE ON backtest_results
    FOR EACH ROW WHEN OLD.result_hash IS NOT NULL
        AND (OLD.summary_json IS NOT NEW.summary_json
             OR OLD.metrics_json IS NOT NEW.metrics_json
             OR OLD.equity_curve_json IS NOT NEW.equity_curve_json
             OR OLD.result_hash IS NOT NEW.result_hash)
    BEGIN
        SELECT RAISE(ABORT, '{BACKTEST_RESULT_MESSAGE}');
    END;
    """,
)

_POSTGRES_DROP: tuple[str, ...] = (
    f"DROP TRIGGER IF EXISTS {BACKTEST_RESULT_TRIGGER} ON backtest_results;",
    "DROP FUNCTION IF EXISTS quantlab_reject_result_mutation();",
    f"DROP TRIGGER IF EXISTS {STRATEGY_VERSION_TRIGGER} ON strategy_versions;",
    "DROP FUNCTION IF EXISTS quantlab_reject_strategy_version_mutation();",
)

_SQLITE_DROP: tuple[str, ...] = (
    f"DROP TRIGGER IF EXISTS {BACKTEST_RESULT_TRIGGER};",
    f"DROP TRIGGER IF EXISTS {STRATEGY_VERSION_TRIGGER};",
)


def statements_for(dialect_name: str) -> tuple[str, ...]:
    """Install statements for a dialect; empty when the dialect has no guards."""

    if dialect_name == "postgresql":
        return _POSTGRES_DDL
    if dialect_name == "sqlite":
        return _SQLITE_DDL
    return ()


def drop_statements_for(dialect_name: str) -> tuple[str, ...]:
    if dialect_name == "postgresql":
        return _POSTGRES_DROP
    if dialect_name == "sqlite":
        return _SQLITE_DROP
    return ()


def install_immutability_triggers(connection: Any) -> list[str]:
    """Install (or reinstall) the guards on ``connection``; returns their names.

    Idempotent on both dialects, and a no-op on any other dialect, so callers can run
    it against every database they create.
    """

    statements = statements_for(connection.dialect.name)
    for statement in statements:
        connection.exec_driver_sql(statement)
    if not statements:
        return []
    return [STRATEGY_VERSION_TRIGGER, BACKTEST_RESULT_TRIGGER]


def drop_immutability_triggers(connection: Any) -> None:
    for statement in drop_statements_for(connection.dialect.name):
        connection.exec_driver_sql(statement)


@event.listens_for(Base.metadata, "after_create")
def _install_guards_after_create(_target: Any, connection: Any, **_kwargs: Any) -> None:
    """Every database created from the models carries the guards (ADR-094)."""

    install_immutability_triggers(connection)
