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
from sqlalchemy.exc import IntegrityError
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


def test_every_revision_can_be_walked_back_down_to_base(tmp_path) -> None:
    """The chain must be reversible, not only forward (ADR-218).

    ``upgrade`` is the only direction anything has ever run: CI, the container
    entrypoint and this file all start at base and walk up. A broken ``downgrade()``
    therefore cannot surface before the night someone has to roll a deployment back.
    This walks the whole chain down one revision at a time — the same step a human
    would take — checking after each step that ``alembic_version`` landed on the
    revision below, and that the last step really left no application tables behind.
    Then it walks back up to head, because a chain that can go down but not up again
    is not a rollback path.
    """

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    from alembic import command
    from app.core.config import settings

    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    # ``walk_revisions()`` starts at the head and walks down to the base revision.
    revisions = [revision.revision for revision in script.walk_revisions()]
    assert head == revisions[0], "walk_revisions() no longer starts at the head"
    assert len(revisions) >= 21, f"the chain shrank to {len(revisions)} revisions"

    url = f"sqlite+pysqlite:///{tmp_path / 'rollback.sqlite3'}"
    original_url = settings.database_url
    settings.database_url = url  # type: ignore[assignment]
    engine = create_engine(url, future=True)
    try:
        command.upgrade(config, "head")
        assert inspect(engine).has_table("strategy_drafts")

        for index, revision in enumerate(revisions):
            command.downgrade(config, "-1")
            with engine.connect() as connection:
                landed = (
                    connection.execute(text("SELECT version_num FROM alembic_version"))
                    .scalars()
                    .all()
                )
            expected = revisions[index + 1] if index + 1 < len(revisions) else None
            if expected is None:
                assert landed == [], f"leaving {revision} left alembic_version at {landed}"
                assert not inspect(engine).has_table("strategy_drafts"), (
                    "the base revision still leaves application tables behind"
                )
            else:
                assert landed == [expected], (
                    f"leaving {revision} landed on {landed}, expected {expected}"
                )

        # And back up again: every revision's upgrade() must still work after a
        # full round trip, not just from a pristine database.
        command.upgrade(config, "head")
        assert inspect(engine).has_table("strategy_drafts")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
            )
    finally:
        engine.dispose()
        settings.database_url = original_url  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 0022: one signal, one order (ADR-221)
# --------------------------------------------------------------------------- #
def _insert_order(connection, signal_id: int | None) -> None:
    """A minimal paper order; foreign keys are not enforced on this SQLite file."""

    connection.execute(
        text(
            "INSERT INTO paper_orders (account_id, signal_id, asset_id, side, order_type, "
            "quantity, status, fees, slippage, created_at) "
            "VALUES (1, :signal_id, 1, 'BUY', 'market', 1, 'filled', 0, 0, "
            "'2026-01-02 00:00:00')"
        ),
        {"signal_id": signal_id},
    )


def test_the_migration_makes_one_signal_mean_one_order(migrated_session: Session) -> None:
    """The index is there, it is unique, and it only covers rows that name a signal."""

    listed = {
        row[0]: row[1]
        for row in migrated_session.execute(
            text("SELECT name, \"unique\" FROM pragma_index_list('paper_orders')")
        ).all()
    }
    assert listed.get("uq_paper_order_signal") == 1, listed
    index_info = text("SELECT name FROM pragma_index_info('uq_paper_order_signal')")
    columns = migrated_session.execute(index_info).scalars().all()
    assert columns == ["signal_id"], columns

    _insert_order(migrated_session, 7001)
    with pytest.raises(IntegrityError):
        _insert_order(migrated_session, 7001)
    migrated_session.rollback()

    # Orders that came from no signal are not part of the rule, and both dialects treat
    # NULLs as distinct, so any number of them still fit.
    _insert_order(migrated_session, None)
    _insert_order(migrated_session, None)
    migrated_session.commit()

    assert (
        migrated_session.execute(
            text("SELECT COUNT(*) FROM paper_orders WHERE signal_id IS NULL")
        ).scalar()
        == 2
    )


def test_the_migration_refuses_to_index_orders_that_already_share_a_signal(tmp_path) -> None:
    """A duplicate stops the upgrade and is named; nothing is deleted, merged or picked."""

    from alembic.config import Config

    from alembic import command
    from app.core.config import settings

    config = Config("alembic.ini")
    url = f"sqlite+pysqlite:///{tmp_path / 'duplicates.sqlite3'}"
    original_url = settings.database_url
    settings.database_url = url  # type: ignore[assignment]
    engine = create_engine(url, future=True)
    try:
        # One revision short of the index, which is the only way to write a duplicate.
        command.upgrade(config, "0021_paper_account_experiment")
        with engine.begin() as connection:
            _insert_order(connection, 4242)
            _insert_order(connection, 4242)

        with pytest.raises(RuntimeError) as failure:
            command.upgrade(config, "head")

        message = str(failure.value)
        assert "signal_id=4242 有 2 笔订单" in message, message
        with engine.connect() as connection:
            kept = connection.execute(
                text("SELECT COUNT(*) FROM paper_orders WHERE signal_id = 4242")
            ).scalar()
        assert kept == 2, "the migration must not delete or merge existing orders"
    finally:
        engine.dispose()
        settings.database_url = original_url  # type: ignore[assignment]
