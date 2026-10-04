"""AI role contracts and task provenance (ADR-152, ADR-153)

Revision ID: 0012_ai_role_contracts
Revises: 0011_signal_closes_direction
Create Date: 2026-10-14 09:00:00.000000

Two changes, both additive:

* ``ai_role_contracts`` indexes the role contract files shipped under
  ``backend/app/ai/contracts/`` so an AI task can be explained after the file it
  used has changed (name + version + content hash + the schema in force).
* ``ai_tasks`` gains the provenance of one call: which role asked, the hash of
  the answer it stored, the untrusted sources it read, and the strategy version
  it talked about. Every column is nullable, so existing rows stay valid and
  SQLite (the local verification database) accepts the migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_ai_role_contracts"
down_revision: str | None = "0011_signal_closes_direction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_role_contracts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=48), nullable=False),
        sa.Column("task_types_json", sa.JSON(), nullable=True),
        sa.Column("required_capabilities_json", sa.JSON(), nullable=True),
        sa.Column("output_language", sa.String(length=16), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("output_schema_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_ai_role_contract"),
    )
    op.add_column("ai_tasks", sa.Column("role", sa.String(length=48), nullable=True))
    op.add_column("ai_tasks", sa.Column("output_hash", sa.String(length=64), nullable=True))
    op.add_column("ai_tasks", sa.Column("source_ids_json", sa.JSON(), nullable=True))
    op.add_column("ai_tasks", sa.Column("research_run_id", sa.Integer(), nullable=True))
    op.add_column("ai_tasks", sa.Column("strategy_version_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_tasks", "strategy_version_id")
    op.drop_column("ai_tasks", "research_run_id")
    op.drop_column("ai_tasks", "source_ids_json")
    op.drop_column("ai_tasks", "output_hash")
    op.drop_column("ai_tasks", "role")
    op.drop_table("ai_role_contracts")
