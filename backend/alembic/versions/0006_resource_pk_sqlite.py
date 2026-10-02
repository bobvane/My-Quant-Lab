"""fix resource monitor primary keys on SQLite

Revision ID: 0006_resource_pk_sqlite
Revises: 0005_signal_outcome_times
Create Date: 2026-10-03 01:00:00.000000

Why this exists
---------------
``0004_resource_monitor`` created the four resource tables with a raw
``sa.BigInteger()`` primary key. The ORM models use
``BigInteger().with_variant(Integer, "sqlite")`` so that SQLite gets ``INTEGER`` —
because only ``INTEGER PRIMARY KEY`` aliases SQLite's rowid and auto-generates a
value. ``BIGINT PRIMARY KEY`` does not, so on SQLite every insert of a resource row
failed with ``NOT NULL constraint failed``.

That surfaced as a *successful* backtest being reported as HTTP 500: the resource
event write runs flush() inside the request, poisoning the session
(see ADR-044, which stopped that failure from spreading). PostgreSQL is unaffected —
``BIGINT``/``BIGSERIAL`` self-generates there, and the deployed NAS records resource
events normally.

``0004`` is intentionally left untouched: it has already been applied in production,
and rewriting an applied revision forks history. This migration corrects the SQLite
DDL instead, and is a no-op everywhere else.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_resource_pk_sqlite"
down_revision: str | None = "0005_signal_outcome_times"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The same variant the ORM models use: BIGINT on PostgreSQL, INTEGER on SQLite.
_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")

_TABLES: tuple[str, ...] = (
    "host_resource_samples",
    "container_resource_samples",
    "resource_rollups",
    "resource_events",
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if not _is_sqlite():
        # PostgreSQL (and anything else) already auto-generates these keys.
        return

    for table in _TABLES:
        # batch_alter_table recreates the table on SQLite, which is the only way to
        # change a column's type there; the copy preserves existing rows.
        with op.batch_alter_table(table) as batch:
            batch.alter_column("id", existing_type=sa.BigInteger(), type_=_PK, nullable=False)


def downgrade() -> None:
    if not _is_sqlite():
        return

    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("id", existing_type=_PK, type_=sa.BigInteger(), nullable=False)
