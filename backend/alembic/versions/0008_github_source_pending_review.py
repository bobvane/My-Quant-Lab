"""remember the commit whose draft is waiting for a human reviewer

Revision ID: 0008_github_source_pending_review
Revises: 0007_backtest_result_warnings
Create Date: 2026-10-03 03:00:00.000000

Why this exists
---------------
The GitHub watcher imports without a human in the loop, and the drafts it builds
never contain exit rules (they are not invented), so the DSL parser rejects every
draft and ``check_source`` used to raise out of the scheduled task: no snapshot, no
status, and every later source left unchecked (ADR-062).

Now that refusal is recorded and the source waits for a human. ``current_commit``
alone cannot carry that wait: it means "the newest commit this source has settled",
and the next beat would see ``head == current_commit`` and report ``unchanged``,
erasing the fact that a review is still outstanding. ``last_import_status`` is a
per-run outcome, so it is overwritten too. The pending commit therefore needs its
own column, and a human importing that commit clears it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_github_source_pending_review"
down_revision: str | None = "0007_backtest_result_warnings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "github_sources",
        sa.Column("pending_review_commit", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("github_sources", "pending_review_commit")
