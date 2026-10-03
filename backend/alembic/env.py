"""Alembic environment.

The database URL always comes from the application settings so migrations run
with the same credentials as the API.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import settings
from app.core.db import Base

# Import the models so ``Base.metadata`` is complete.
from app.domain import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL is *not* pushed into `config`: alembic's ConfigParser interpolates
# `%`, so a password containing one raised `ValueError: invalid interpolation
# syntax` before a connection was attempted, and storing it there made a second
# copy of a fact the settings already hold (ADR-098).
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Built straight from the settings: `engine_from_config` would read back the
    # URL we deliberately do not store in the config (ADR-098).
    connectable = create_engine(settings.database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
