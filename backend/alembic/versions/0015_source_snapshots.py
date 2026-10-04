"""Source snapshots: what an external source looked like when we read it (ADR-163)

Revision ID: 0015_source_snapshots
Revises: 0014_artifact_source_hash
Create Date: 2026-10-06 21:00:00.000000

One new table and one nullable column, both additive:

* ``ai_source_snapshots`` records one observation of an external source — the URL,
  the HTTP metadata, the hash of the bytes, the hash of the text read out of them,
  the parser and its version, the retention decision and any error. Rows are
  appended, never overwritten: fetching the same URL twice leaves two snapshots, so
  a later question ("which material did this run read?") has an answer.
* ``research_artifacts.snapshot_id`` points at the observation an artifact came out
  of. It is nullable because a caller-supplied text source never fetched anything,
  and it does not touch ``source_hash`` / ``text_hash`` (ADR-161): those stay the
  identity of the material and of the text a run actually read.

There is deliberately no column for the third-party full text here. The excerpt is
bounded by the retention policy in ``app/ai/research.py``; a snapshot is an
observation, not a second copy of somebody else's document.

``batch_alter_table(recreate="auto")`` keeps PostgreSQL on a plain ALTER TABLE and
only rebuilds the table on dialects that cannot add a constraint in place (SQLite).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_source_snapshots"
down_revision: str | None = "0014_artifact_source_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_source_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("parse_status", sa.String(length=16), nullable=False, server_default="not_parsed"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("chars_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=True),
        sa.Column("parser", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("robots_ok", sa.Boolean(), nullable=True),
        sa.Column("retention", sa.String(length=16), nullable=False, server_default="excerpt"),
        sa.Column("retained_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("license_note", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_source_snapshots_url_time", "ai_source_snapshots", ["url", "created_at"])

    with op.batch_alter_table("research_artifacts", recreate="auto") as batch:
        batch.add_column(sa.Column("snapshot_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_research_artifacts_snapshot_id",
            "ai_source_snapshots",
            ["snapshot_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("research_artifacts", recreate="auto") as batch:
        batch.drop_constraint("fk_research_artifacts_snapshot_id", type_="foreignkey")
        batch.drop_column("snapshot_id")
    op.drop_index("ix_ai_source_snapshots_url_time", table_name="ai_source_snapshots")
    op.drop_table("ai_source_snapshots")
