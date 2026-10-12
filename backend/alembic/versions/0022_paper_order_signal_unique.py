"""one signal can only produce one paper order

Revision ID: 0022_paper_order_signal_unique
Revises: 0021_paper_account_experiment
Create Date: 2026-10-16 09:00:00.000000

Why this exists
---------------
``execute_signal`` used to *read* ``paper_orders`` for the signal, find nothing,
and then write a row. Two requests doing that at the same time both read nothing
and both wrote, so one signal could collect two orders, two cash movements and two
ledger rows (ADR-221). The rule is now one signal, one order: a repeat execution
answers with the order the first execution created, and writes nothing.

The rule has to live in the database, not only in Python: a read-then-write check
cannot see a row another transaction has not committed yet. A unique index is the
smallest thing that can refuse the second writer.

Deliberately additive: one index, no column, no data change. ``signal_id`` stays
nullable so orders that came from no signal are untouched, and SQLite and
PostgreSQL both treat NULLs as distinct in a unique index, so any number of those
still fit.

The pre-flight check below refuses to add the index when the invariant is already
broken. It deletes, merges and rewrites nothing: what a duplicated signal means
for an account's ledger is a decision for a human, and guessing it would be
inventing history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_paper_order_signal_unique"
down_revision: str | None = "0021_paper_account_experiment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT signal_id, COUNT(*) FROM paper_orders "
                "WHERE signal_id IS NOT NULL "
                "GROUP BY signal_id HAVING COUNT(*) > 1 "
                "ORDER BY signal_id"
            )
        )
        .fetchall()
    )
    if duplicates:
        # Chinese on purpose: whoever reads this is deploying, and the message has
        # to say what is wrong, which signals are affected, and that nothing was
        # cleaned up automatically.
        listing = "、".join(f"signal_id={row[0]} 有 {row[1]} 笔订单" for row in duplicates)
        raise RuntimeError(
            "paper_orders 里已经存在同一个 signal_id 的多笔订单，「一个信号只产生一笔订单」"
            f"的唯一约束暂时加不上去：{listing}。这条迁移不会删除、合并或改写任何订单——"
            "请先人工确认这些订单是怎么产生的、账本应该怎么算，再决定如何处理。"
        )

    # A plain ``CREATE UNIQUE INDEX``, which both dialects support natively: no
    # table rebuild, so the ADR-219 landmine (batch mode copying ``paper_orders``
    # while ``paper_trades.order_id`` points at it) is not triggered here.
    op.create_index("uq_paper_order_signal", "paper_orders", ["signal_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_paper_order_signal", table_name="paper_orders")
