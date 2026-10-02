"""store the engine's warnings with the backtest result

Revision ID: 0007_backtest_result_warnings
Revises: 0006_resource_pk_sqlite
Create Date: 2026-10-03 02:00:00.000000

Why this exists
---------------
``run_backtest`` has always reported warnings -- a parameter override it had to ignore
because the strategy does not declare it, or a warm-up longer than the data it was given.
``POST /backtests`` returned them once and nothing kept them, while
``GET /backtests/{id}`` answered ``warnings: []`` outright (ADR-054). Reloading the page
therefore erased the only notice that the numbers under it were computed on data too short
for the strategy, or that an input the caller sent had been dropped.

The column is added with a ``'[]'`` server default so existing rows stay readable; the ORM
model keeps its own ``default=list`` for new rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_backtest_result_warnings"
down_revision: str | None = "0006_resource_pk_sqlite"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtest_results",
        sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("backtest_results", "warnings_json")
