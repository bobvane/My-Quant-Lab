"""Resource-monitor primary keys must auto-generate on SQLite too (docs/20, ADR-048).

Background: ``0004_resource_monitor`` created the four resource tables with a raw
``sa.BigInteger()`` primary key, while the ORM models use
``BigInteger().with_variant(Integer, "sqlite")``. Only ``INTEGER PRIMARY KEY`` aliases
SQLite's rowid, so ``BIGINT PRIMARY KEY`` never auto-generated a value and every
resource insert failed with ``NOT NULL constraint failed: resource_events.id``.

In a request that surfaced as a *successful* backtest being reported as HTTP 500
(fixed separately in ADR-044). These tests run the REAL migration chain against SQLite
and then insert without an explicit id, which is the precise operation that failed.

v2.6.0 deleted the resource-monitor feature — its ORM models, collector, store and API —
but the four tables of the already-applied migrations stay in the database: the code is
gone, the schema is not (no ``DROP TABLE``, no ``0021``). These tests therefore drive
the tables with raw SQL, which is also the standing proof that the applied DDL is still
insertable for as long as the tables exist.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


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

    migrated_session.execute(
        text("INSERT INTO resource_events (event_key, event_type) VALUES (:key, :kind)"),
        {"key": "test:migrated", "kind": "unit_test"},
    )
    migrated_session.commit()

    row = migrated_session.execute(
        text("SELECT id, event_key FROM resource_events WHERE event_key = :key"),
        {"key": "test:migrated"},
    ).one()
    assert row.id is not None
    assert row.id > 0
    assert row.event_key == "test:migrated"


def test_second_insert_gets_a_new_id(migrated_session: Session) -> None:
    """Auto-generation must keep advancing, not reuse rowid 1."""

    for key in ("test:seq1", "test:seq2"):
        migrated_session.execute(
            text("INSERT INTO resource_events (event_key, event_type) VALUES (:key, :kind)"),
            {"key": key, "kind": "unit_test"},
        )
    migrated_session.commit()

    ids = (
        migrated_session.execute(
            text("SELECT id FROM resource_events WHERE event_key IN ('test:seq1', 'test:seq2')")
        )
        .scalars()
        .all()
    )
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert max(ids) >= 2


def test_sibling_resource_tables_autogenerate_too(migrated_session: Session) -> None:
    """All four tables got the same treatment, so check them all."""

    now = dt.datetime.now(tz=dt.UTC).isoformat()
    migrated_session.execute(
        text("INSERT INTO host_resource_samples (ts, cpu_percent) VALUES (:ts, :cpu)"),
        {"ts": now, "cpu": 12.5},
    )
    migrated_session.execute(
        text(
            "INSERT INTO container_resource_samples (ts, container_name, is_quantlab) "
            "VALUES (:ts, :name, :flag)"
        ),
        {"ts": now, "name": "quantlab-app", "flag": True},
    )
    migrated_session.execute(
        text(
            "INSERT INTO resource_rollups (granularity, bucket_start, scope, cpu_avg) "
            "VALUES (:grain, :bucket, :scope, :cpu)"
        ),
        {"grain": "5m", "bucket": now, "scope": "host", "cpu": 12.5},
    )
    migrated_session.commit()

    for table in ("host_resource_samples", "container_resource_samples", "resource_rollups"):
        ids = migrated_session.execute(text(f"SELECT id FROM {table}")).scalars().all()
        assert ids, f"{table} accepted no row"
        assert all(row_id and row_id > 0 for row_id in ids), table


def test_sqlite_column_type_is_integer(migrated_session: Session) -> None:
    """The DDL, not just the behaviour: BIGINT PRIMARY KEY is the root cause."""

    rows = migrated_session.execute(
        text("SELECT name, type FROM pragma_table_info('resource_events') WHERE name = 'id'")
    ).all()
    assert rows, "resource_events.id not found"
    assert rows[0][1].upper() == "INTEGER", rows
