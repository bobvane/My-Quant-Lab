"""signal closes_direction: an exit names the position it closes (ADR-115)

Revision ID: 0011_signal_closes_direction
Revises: 0010_immutability
Create Date: 2026-10-12 09:00:00.000000

``signals.direction`` was written for exits as ``FLAT``, and ``FLAT`` is also what
a signal that closes nothing carries. Two different facts shared one value, so the
outcome evaluator scored an exit against the wrong sign and the paper ledger tried
to sell a long it did not hold. The new nullable column carries the missing fact
("this SELL closes a LONG") and stays NULL for entries.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_signal_closes_direction"
down_revision: str | None = "0010_immutability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("closes_direction", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "closes_direction")
