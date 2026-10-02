"""signal outcome entry/exit timestamps (docs/11)

Revision ID: 0005_signal_outcome_times
Revises: 0004_resource_monitor
Create Date: 2026-10-01 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_signal_outcome_times"
down_revision: str | None = "0004_resource_monitor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal_outcomes", sa.Column("entry_time", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "signal_outcomes", sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("signal_outcomes", "exit_time")
    op.drop_column("signal_outcomes", "entry_time")
