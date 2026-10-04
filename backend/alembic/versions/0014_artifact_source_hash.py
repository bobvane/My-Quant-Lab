"""Research artifacts keep the hash of the material itself (ADR-161)

Revision ID: 0014_artifact_source_hash
Revises: 0013_research_layer
Create Date: 2026-10-05 18:00:00.000000

One additive column. ``research_artifacts`` already stored ``text_hash``, the
hash of the version a run actually read; it could not say anything about the
material the caller handed over. After this revision the two are separate, so a
truncated source is visible as ``source_hash != text_hash`` and a citation can
state which text it was verified against.

Nothing is executable here, and no existing row changes meaning: the column is
nullable, and older artifacts simply have no source hash.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_artifact_source_hash"
down_revision: str | None = "0013_research_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_artifacts",
        sa.Column("source_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_artifacts", "source_hash")
