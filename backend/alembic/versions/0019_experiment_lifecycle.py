"""experiment lifecycle: drafts, archival, and the frozen run configuration

Revision ID: 0019_experiment_lifecycle
Revises: 0018_run_progress_paper_binding
Create Date: 2026-10-08 12:40:00.000000

Why this exists
---------------
``strategy_experiments`` could say what a research run produced, but not what the
run was configured with, nor where it sits in its own life:

* ``updated_at`` / ``archived_at``. An experiment now has a lifecycle beyond
  running: it can be stored as a draft, renamed, run later, and archived without
  being deleted (ADR-182). ``status`` already carried the string; the timestamps
  are what let a client say *when* it changed, and archive keeps the history
  instead of destroying it.
* ``initial_capital`` / ``start_date`` / ``end_date``. The capital and the bar
  window belong to the experiment, not to the strategy's current defaults: they
  are written once from the validated request and never re-derived, so re-reading
  an old experiment keeps reporting the numbers it actually ran with (ADR-183).
  This is the same "store, never recompute" rule the rest of the table follows.

Deliberately additive: five nullable columns, no index (the existing
``ix_strategy_experiments_status`` already covers the list ordering), no data
rewrite, no server default -- a row that predates this revision simply has NULLs
rather than a fabricated backfill. That keeps the same chain valid on SQLite
(tests) and PostgreSQL (production).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_experiment_lifecycle"
down_revision: str | None = "0018_run_progress_paper_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_experiments",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "strategy_experiments",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "strategy_experiments",
        sa.Column("initial_capital", sa.Numeric(precision=20, scale=8), nullable=True),
    )
    op.add_column(
        "strategy_experiments",
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "strategy_experiments",
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategy_experiments", "end_date")
    op.drop_column("strategy_experiments", "start_date")
    op.drop_column("strategy_experiments", "initial_capital")
    op.drop_column("strategy_experiments", "archived_at")
    op.drop_column("strategy_experiments", "updated_at")
