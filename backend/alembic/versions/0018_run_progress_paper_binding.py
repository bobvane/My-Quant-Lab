"""run progress, and a paper account opened from a backtest

Revision ID: 0018_run_progress_paper_binding
Revises: 0017_ai_config_history_snapshots
Create Date: 2026-10-08 10:20:00.000000

Why this exists
---------------
Two halves of the same story: a backtest that can be watched, and a paper
account that knows which backtest it came from.

* ``backtest_runs.progress`` / ``current_step`` (ADR-180). A run already had
  ``status`` (pending/running/completed/failed) but no way to say *how far* it
  had come, so a client could only show a spinner. The values are walked by the
  synchronous run itself and by the optional worker path, so a client that polls
  ``GET /backtests/{id}`` reads the same shape either way.
* ``paper_accounts.strategy_version_id`` / ``backtest_run_id`` /
  ``parameters_json`` (ADR-181). ``strategy_id`` alone could not answer "which
  version, with which parameters, did I copy into paper?" -- and comparing a
  paper result with the backtest that motivated it needs exactly that. The run
  reference is nullable and never cascades: retiring a run must not delete an
  account that copied it.

Deliberately additive: no column is dropped and no constraint is rebuilt, so the
same chain runs on SQLite (tests) and PostgreSQL (production). The JSON column
gets a ``'{}'`` server default so rows that existed before this revision stay
readable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_run_progress_paper_binding"
down_revision: str | None = "0017_ai_config_history_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtest_runs",
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("backtest_runs", sa.Column("current_step", sa.String(length=64), nullable=True))

    op.add_column("paper_accounts", sa.Column("strategy_version_id", sa.Integer(), nullable=True))
    op.add_column("paper_accounts", sa.Column("backtest_run_id", sa.Integer(), nullable=True))
    op.add_column(
        "paper_accounts",
        sa.Column(
            "parameters_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # SQLite refuses ``ALTER TABLE ... ADD CONSTRAINT``, so a column added with a
    # foreign key cannot carry one there; PostgreSQL can, and production is the
    # dialect where referential integrity actually has to hold. The ORM model
    # declares both keys either way (0006 and 0017 set the same precedent).
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_paper_accounts_strategy_version_id",
            "paper_accounts",
            "strategy_versions",
            ["strategy_version_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_paper_accounts_backtest_run_id",
            "paper_accounts",
            "backtest_runs",
            ["backtest_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_column("paper_accounts", "parameters_json")
    op.drop_column("paper_accounts", "backtest_run_id")
    op.drop_column("paper_accounts", "strategy_version_id")
    op.drop_column("backtest_runs", "current_step")
    op.drop_column("backtest_runs", "progress")
