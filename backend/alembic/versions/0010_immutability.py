"""install the immutability guards from the shared definition

Revision ID: 0010_immutability
Revises: 0009_drop_dead_schema
Create Date: 2026-10-11 21:10:00.000000

``0002_immutability`` installed the two guards, but only when the dialect was
PostgreSQL, and only in that one migration. Every database built by
``Base.metadata.create_all()`` — the unit suite, the probe scripts, a developer's
scratch database — therefore had no guard at all, which is exactly why the
``operator does not exist: json = json`` defect (fixed by ``0003_fix_triggers_json``)
could only ever be discovered by the release smoke test.

The guards now live in ``app/domain/immutability.py`` and are installed by
``Base.metadata``'s ``after_create`` event (ADR-094). This revision runs the same
function over a migrated database, so "the schema a migration builds" and "the schema
the models build" end up identical. ``0002`` and ``0003`` stay in the history as the
record of how the guard got here; this is the definition that the final state follows.

The downgrade deliberately does nothing: an invariant may not be weakened by rolling a
migration back (`0003_fix_triggers_json` takes the same position).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.domain.immutability import install_immutability_triggers

revision: str = "0010_immutability"
down_revision: str | None = "0009_drop_dead_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    install_immutability_triggers(op.get_bind())


def downgrade() -> None:
    # Intentionally empty: the guards are an invariant of the data model, not of a
    # revision, and dropping them would make a rolled-back database less safe than a
    # freshly created one.
    return None
