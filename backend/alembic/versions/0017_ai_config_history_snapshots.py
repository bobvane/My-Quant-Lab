"""AI configuration history snapshots

Revision ID: 0017_ai_config_history_snapshots
Revises: 0016_strategy_experiments
Create Date: 2026-10-07 18:30:00.000000

An AI provider or model is *current configuration* and may be deleted; an AI
task or usage row is *history* and may not. Both history tables used to point at
the configuration only through a foreign key, so deleting a provider had to be
refused outright (ADR-083). This revision decouples the two lifecycles by adding
the provider/model **name snapshots** the history rows need to stay readable
once the configuration is gone (ADR-177), and backfilling them from the
configuration still on file.

Deliberately additive: no constraint is dropped or rebuilt. The foreign keys
created in ``0001`` are unnamed on SQLite and cannot be removed there without
recreating the table, and the delete path in the application clears the keys
itself, so the schema does not have to carry that change.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_ai_config_history_snapshots"
down_revision: str | None = "0016_strategy_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_tasks", sa.Column("provider_name", sa.String(length=64), nullable=True))
    op.add_column("ai_tasks", sa.Column("model_name", sa.String(length=128), nullable=True))
    op.add_column("ai_usage", sa.Column("provider_name", sa.String(length=64), nullable=True))
    op.add_column("ai_usage", sa.Column("model_name", sa.String(length=128), nullable=True))

    # The backfill is written with portable SQLAlchemy Core statements instead of
    # dialect-specific UPDATE ... FROM, because the same chain runs on SQLite
    # (tests) and PostgreSQL (production).
    bind = op.get_bind()
    providers = sa.table(
        "ai_providers", sa.column("id", sa.Integer), sa.column("name", sa.String)
    )
    models = sa.table(
        "ai_models", sa.column("id", sa.Integer), sa.column("model_name", sa.String)
    )
    provider_names = {
        row.id: row.name for row in bind.execute(sa.select(providers.c.id, providers.c.name))
    }
    model_names = {
        row.id: row.model_name for row in bind.execute(sa.select(models.c.id, models.c.model_name))
    }

    for table_name in ("ai_tasks", "ai_usage"):
        history = sa.table(
            table_name,
            sa.column("id", sa.Integer),
            sa.column("provider_id", sa.Integer),
            sa.column("model_id", sa.Integer),
            sa.column("provider_name", sa.String),
            sa.column("model_name", sa.String),
        )
        rows = list(
            bind.execute(sa.select(history.c.id, history.c.provider_id, history.c.model_id))
        )
        for row in rows:
            provider_name = provider_names.get(row.provider_id)
            model_name = model_names.get(row.model_id)
            if provider_name is None and model_name is None:
                continue
            bind.execute(
                history.update()
                .where(history.c.id == row.id)
                .values(provider_name=provider_name, model_name=model_name)
            )


def downgrade() -> None:
    op.drop_column("ai_usage", "model_name")
    op.drop_column("ai_usage", "provider_name")
    op.drop_column("ai_tasks", "model_name")
    op.drop_column("ai_tasks", "provider_name")
