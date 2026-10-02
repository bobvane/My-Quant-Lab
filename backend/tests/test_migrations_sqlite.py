"""Resource-monitor primary keys must auto-generate on SQLite too (docs/20, ADR-048).

Background: ``0004_resource_monitor`` created the four resource tables with a raw
``sa.BigInteger()`` primary key, while the ORM models use
``BigInteger().with_variant(Integer, "sqlite")``. Only ``INTEGER PRIMARY KEY`` aliases
SQLite's rowid, so ``BIGINT PRIMARY KEY`` never auto-generated a value and every
resource insert failed with ``NOT NULL constraint failed: resource_events.id``.

In a request that surfaced as a *successful* backtest being reported as HTTP 500
(fixed separately in ADR-044). These tests run the REAL migration chain against SQLite
and then insert without an explicit id, which is the precise operation that failed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domain.models import (
    ContainerResourceSample,
    HostResourceSample,
    ResourceEvent,
    ResourceRollup,
)


@pytest.fixture(scope="module")
def sqlite_migrated_url(tmp_path_factory):
    """Run the real alembic chain against a scratch SQLite file."""

    from app.core.config import settings

    db_path = tmp_path_factory.mktemp("migrated") / "app.sqlite3"
    url = f"sqlite+pysqlite:///{db_path}"

    original_url = settings.database_url
    settings.database_url = url  # type: ignore[assignment]
    try:
        from alembic.config import Config

        from alembic import command

        command.upgrade(Config("alembic.ini"), "head")
        yield url
    finally:
        settings.database_url = original_url  # type: ignore[assignment]


@pytest.fixture()
def migrated_session(sqlite_migrated_url):
    engine = create_engine(sqlite_migrated_url, future=True)
    session = Session(engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_resource_events_id_autogenerates(migrated_session: Session) -> None:
    """The exact insert that used to raise NOT NULL constraint failed."""

    row = ResourceEvent(event_key="test:migrated", event_type="unit_test")
    migrated_session.add(row)
    migrated_session.commit()

    assert row.id is not None
    assert row.id > 0
    stored = migrated_session.get(ResourceEvent, row.id)
    assert stored is not None and stored.event_key == "test:migrated"


def test_second_insert_gets_a_new_id(migrated_session: Session) -> None:
    """Auto-generation must keep advancing, not reuse rowid 1."""

    first = ResourceEvent(event_key="test:seq1", event_type="unit_test")
    second = ResourceEvent(event_key="test:seq2", event_type="unit_test")
    migrated_session.add_all([first, second])
    migrated_session.commit()
    assert second.id != first.id
    assert max(first.id, second.id) >= 2


def test_sibling_resource_tables_autogenerate_too(migrated_session: Session) -> None:
    """All four tables got the same treatment, so check them all."""

    now = dt.datetime.now(tz=dt.UTC)
    host = HostResourceSample(ts=now, cpu_percent=12.5)
    container = ContainerResourceSample(ts=now, container_name="quantlab-api", is_quantlab=True)
    rollup = ResourceRollup(granularity="5m", bucket_start=now, scope="host", cpu_avg=12.5)
    migrated_session.add_all([host, container, rollup])
    migrated_session.commit()

    assert host.id and container.id and rollup.id


def test_sqlite_column_type_is_integer(migrated_session: Session) -> None:
    """The DDL, not just the behaviour: BIGINT PRIMARY KEY is the root cause."""

    rows = migrated_session.execute(
        text("SELECT name, type FROM pragma_table_info('resource_events') WHERE name = 'id'")
    ).all()
    assert rows, "resource_events.id not found"
    assert rows[0][1].upper() == "INTEGER", rows
