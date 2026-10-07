"""Strategy experiments: a research run becomes a persisted entity

Revision ID: 0016_strategy_experiments
Revises: 0015_source_snapshots
Create Date: 2026-10-07 09:00:00.000000

Two new tables, both additive (ADR-174):

* ``strategy_experiments`` is the durable record of one research run -- the
  strategy version and dataset it used, the validated request, the parameter
  configuration, a status (``running`` / ``completed`` / ``failed``) and a
  summary. Before this, a sensitivity sweep or a Monte Carlo simulation existed
  only inside the HTTP response that produced it; the numbers were unrecoverable
  the moment the caller closed the tab.
* ``experiment_results`` is one measured point. For a sweep it carries the
  parameter <-> result pair (``parameters_json`` next to ``metrics_json`` and the
  engine's own ``payload_json``), which is exactly what a sweep is for and what a
  flat metrics list cannot express. ``backtest_run_id`` keeps the lineage to the
  persisted ``backtest_runs`` artefact when the point produced one, without
  taking ownership of it: deleting an experiment drops its points but never the
  backtest, which is its own artefact.

Execution stays synchronous inside the request, exactly like ``POST /backtests``:
there is no worker, no queue and no new setting.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_strategy_experiments"
down_revision: str | None = "0015_source_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Creation order is dependency order: ``strategy_versions`` / ``market_data``
    # exist from earlier revisions, and ``experiment_results`` must follow
    # ``strategy_experiments`` because it points a foreign key at it.
    op.create_table(
        "strategy_experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"]),
        sa.ForeignKeyConstraint(["series_id"], ["market_data.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_strategy_experiments_status", "strategy_experiments", ["status", "created_at"]
    )
    op.create_index(
        "ix_strategy_experiments_version",
        "strategy_experiments",
        ["strategy_version_id", "created_at"],
    )

    op.create_table(
        "experiment_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("parameters_json", sa.JSON(), nullable=True),
        sa.Column("backtest_run_id", sa.Integer(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["strategy_experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["backtest_run_id"], ["backtest_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_results_experiment", "experiment_results", ["experiment_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_results_experiment", table_name="experiment_results")
    op.drop_table("experiment_results")
    op.drop_index("ix_strategy_experiments_version", table_name="strategy_experiments")
    op.drop_index("ix_strategy_experiments_status", table_name="strategy_experiments")
    op.drop_table("strategy_experiments")
