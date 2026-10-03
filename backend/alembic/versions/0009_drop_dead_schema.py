"""drop the tables and columns nobody ever wrote or read

Revision ID: 0009_drop_dead_schema
Revises: 0008_github_pending_review
Create Date: 2026-10-11 21:00:00.000000

Nothing in this repository ever wrote a row into ``features``, ``jobs`` or ``job_logs``,
and nothing reads them either: ``FeatureDefinition`` had two read endpoints that
therefore always answered an empty list, and ``Job``/``JobLog`` had no endpoint at all.
The feature catalogue is now served from code (``app/features/catalogue.py``, ADR-093),
so the table can go. Three columns are dead in exactly the same way (ADR-095):
``backtest_runs.parameters_id`` never held anything — the parameters a run really used
are in ``parameters_json`` — and ``ai_models.context_length`` /
``ai_models.supports_structured_output`` have no reader anywhere.

A column or table nobody reads is not a capability, it is a liability: it invites a
future reader to trust a field that can only ever be NULL.

The downgrade recreates what the upgrade dropped, from the shapes ``0001_initial``
declared, because the PostgreSQL regression suite reconciles the whole chain with
``downgrade("base")`` and a half-restored schema would poison the next run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_drop_dead_schema"
down_revision: str | None = "0008_github_pending_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``job_logs`` references ``jobs``, so it goes first.
    op.drop_table("job_logs")
    op.drop_table("jobs")
    op.drop_table("features")

    # ``recreate="auto"`` keeps PostgreSQL on a plain ALTER TABLE and only rebuilds the
    # table where the dialect cannot drop a column in place (older SQLite).
    with op.batch_alter_table("backtest_runs", recreate="auto") as batch:
        batch.drop_column("parameters_id")
    with op.batch_alter_table("ai_models", recreate="auto") as batch:
        batch.drop_column("context_length")
        batch.drop_column("supports_structured_output")


def downgrade() -> None:
    with op.batch_alter_table("ai_models", recreate="auto") as batch:
        batch.add_column(sa.Column("context_length", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("supports_structured_output", sa.Boolean(), nullable=False)
        )
    op.add_column("backtest_runs", sa.Column("parameters_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_backtest_runs_parameters_id",
        "backtest_runs",
        "strategy_parameters",
        ["parameters_id"],
        ["id"],
    )

    op.create_table(
        "features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("feature_type", sa.String(length=32), nullable=False),
        sa.Column("feature_version", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("is_deterministic", sa.Boolean(), nullable=False),
        sa.Column("lookahead_safe", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "job_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
