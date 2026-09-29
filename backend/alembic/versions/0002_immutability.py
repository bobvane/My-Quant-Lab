"""enforce strategy version immutability and reproducibility

Revision ID: 0002_immutability
Revises: 0001_initial
Create Date: 2026-01-01

Strategy versions must never change once written: historical backtests reference
them, so mutating a row would silently invalidate published results. The rule is
enforced in the database (not only in the service layer) so a direct SQL update
cannot corrupt history. The trigger is skipped on SQLite, which is only used by
the unit test suite.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_immutability"
down_revision: str | None = "0001_initial"
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
            IF NEW.dsl_json IS DISTINCT FROM OLD.dsl_json
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
        CREATE TRIGGER trg_strategy_versions_immutable
        BEFORE UPDATE ON strategy_versions
        FOR EACH ROW
        EXECUTE FUNCTION quantlab_reject_strategy_version_mutation();
        """
    )

    # Completed backtest results are append-only as well.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION quantlab_reject_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.result_hash IS NOT NULL
               AND (NEW.summary_json IS DISTINCT FROM OLD.summary_json
                    OR NEW.metrics_json IS DISTINCT FROM OLD.metrics_json
                    OR NEW.equity_curve_json IS DISTINCT FROM OLD.equity_curve_json
                    OR NEW.result_hash IS DISTINCT FROM OLD.result_hash) THEN
                RAISE EXCEPTION 'completed backtest results are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_backtest_results_immutable
        BEFORE UPDATE ON backtest_results
        FOR EACH ROW
        EXECUTE FUNCTION quantlab_reject_result_mutation();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_backtest_results_immutable ON backtest_results;")
    op.execute("DROP FUNCTION IF EXISTS quantlab_reject_result_mutation();")
    op.execute("DROP TRIGGER IF EXISTS trg_strategy_versions_immutable ON strategy_versions;")
    op.execute("DROP FUNCTION IF EXISTS quantlab_reject_strategy_version_mutation();")
