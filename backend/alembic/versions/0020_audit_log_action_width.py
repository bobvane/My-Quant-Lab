"""widen audit_logs.action so the adoption ledger entry fits

Revision ID: 0020_audit_log_action_width
Revises: 0019_experiment_lifecycle
Create Date: 2026-10-08 20:40:00.000000

Why this exists
---------------
``POST /experiments/from-backtest/{run_id}`` writes one ledger entry per adoption whose
``action`` is ``strategy_experiment_adopted_from_backtest`` -- 41 characters. The column was
created as ``VARCHAR(32)`` in ``0001_initial_schema``, so PostgreSQL refused the write:

    psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)

The adoption transaction rolls back (no half-written experiment is left behind) and the
endpoint answers HTTP 500 -- which is what the deployed NAS did for *every* run that had not
been adopted yet, while the run's own 201 path kept working. SQLite, which every local test
and the whole E2E harness run on, stores the same string without complaint, so the suite
stayed green while production was broken. This is ADR-064's trap in a second place: ADR-186
records it and adds the always-on guard ``tests/test_audit_action_length.py``, which reads
the width out of the model and the migration chain instead of trusting a green SQLite run.

The column is widened to 64 rather than shortening the action name: the ledger is supposed
to say what happened, and 64 matches the neighbouring ``event_type``. Widening never
truncates, so existing rows (all of them shorter than 32 today) are left exactly as they
are -- no data rewrite, no backfill, no index change.

``0001`` is deliberately left alone: it has already been applied in production, and editing
an applied revision forks history (the same reasoning as ``0006_resource_pk_sqlite``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_audit_log_action_width"
down_revision: str | None = "0019_experiment_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = sa.String(length=32)
_NEW = sa.String(length=64)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        # SQLite has no ALTER COLUMN TYPE and ignores the length anyway; batch mode
        # recreates the table, which is the only way to change a column's type there, so a
        # freshly migrated SQLite schema still agrees with the model (ADR-186).
        with op.batch_alter_table("audit_logs") as batch:
            batch.alter_column(
                "action", existing_type=_OLD, type_=_NEW, existing_nullable=False
            )
        return

    op.alter_column(
        "audit_logs", "action", existing_type=_OLD, type_=_NEW, existing_nullable=False
    )


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("audit_logs") as batch:
            batch.alter_column(
                "action", existing_type=_NEW, type_=_OLD, existing_nullable=False
            )
        return

    # Narrowing back fails loudly when a row already holds something longer than 32
    # characters, which is the honest outcome: silently truncating a ledger entry would be
    # worse than refusing the downgrade.
    op.alter_column(
        "audit_logs", "action", existing_type=_NEW, type_=_OLD, existing_nullable=False
    )
