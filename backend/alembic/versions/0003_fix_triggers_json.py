"""fix immutability triggers for PostgreSQL json columns

Revision ID: 0003_fix_triggers_json
Revises: 0002_immutability
Create Date: 2026-09-30

Root cause of CI smoke-test 500s on POST /strategies/{id}/versions:

    psycopg.errors.UndefinedFunction: operator does not exist: json = json
    CONTEXT: PL/pgSQL function quantlab_reject_strategy_version_mutation()

``IS DISTINCT FROM`` needs an equality operator for the operand type, but
PostgreSQL's ``json`` type has none (only ``jsonb`` does). Every UPDATE on
``strategy_versions`` — including the routine ``is_current`` flip performed by
``create_strategy_version`` — crashed the trigger, so even the very first
version creation returned 500.

Why the unit suite never caught it: the trigger is only installed on
PostgreSQL (0002 returns early on SQLite), and every local test runs SQLite.

Fix: compare the ``::text`` rendering instead. For an immutability guard this
is the strictest correct semantics — any byte-level change counts as mutation —
and ``IS DISTINCT FROM`` still handles NULLs properly.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_fix_triggers_json"
down_revision: str | None = "0002_immutability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION quantlab_reject_strategy_version_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.dsl_json::text IS DISTINCT FROM OLD.dsl_json::text
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.immutable_hash IS DISTINCT FROM OLD.immutable_hash THEN
                RAISE EXCEPTION
                    'strategy_versions rows are immutable: create a new version instead';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION quantlab_reject_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.result_hash IS NOT NULL
               AND (NEW.summary_json::text IS DISTINCT FROM OLD.summary_json::text
                    OR NEW.metrics_json::text IS DISTINCT FROM OLD.metrics_json::text
                    OR NEW.equity_curve_json::text IS DISTINCT FROM OLD.equity_curve_json::text
                    OR NEW.result_hash IS DISTINCT FROM OLD.result_hash) THEN
                RAISE EXCEPTION 'completed backtest results are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    # Reinstalling the 0002 bodies would reintroduce the crash; downgrade is a
    # no-op by design. Immutability must never be weakened by a migration.
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    return None
