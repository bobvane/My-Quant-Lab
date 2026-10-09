"""a paper account remembers the experiment it was opened from

Revision ID: 0021_paper_account_experiment
Revises: 0020_audit_log_action_width
Create Date: 2026-10-09 21:40:00.000000

Why this exists
---------------
``paper_accounts.backtest_run_id`` (0018, ADR-181) already records *what* an
account was copied from, but not *why it was copied at all*. The "实验" page's
button is 「用这条实验创建模拟账户」: the user starts from an experiment, and the
account that comes out of it can only say 「来自回测 #N」 — the experiment id is
dropped on the floor, so nothing downstream can walk back to the record the user
actually acted on. That is the same broken half ADR-207 fixed for strategies and
signals, one hop further along the chain (ADR-209).

The column is nullable and carries ``ondelete="SET NULL"``, exactly like the run
id next to it: deleting an experiment must not delete an account that was opened
from it, and it must not leave a dangling reference either — the account stands,
with one fewer thing to point at.

Deliberately additive: one column, no rebuild, no backfill. Rows that existed
before this revision read ``NULL``, which is the honest answer — they were
created by a path that did not record the experiment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_paper_account_experiment"
down_revision: str | None = "0020_audit_log_action_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("paper_accounts", sa.Column("experiment_id", sa.Integer(), nullable=True))

    # SQLite refuses ``ALTER TABLE ... ADD CONSTRAINT``, so a column added with a
    # foreign key cannot carry one there; PostgreSQL can, and production is the
    # dialect where referential integrity actually has to hold (0018 set the same
    # precedent). The ORM model declares the key either way.
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_paper_accounts_experiment_id",
            "paper_accounts",
            "strategy_experiments",
            ["experiment_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_paper_accounts_experiment_id", "paper_accounts", type_="foreignkey")
    op.drop_column("paper_accounts", "experiment_id")
