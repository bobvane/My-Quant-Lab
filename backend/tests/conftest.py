"""Shared pytest fixtures.

Tests run against an in-memory SQLite database so the suite needs no external
services. PostgreSQL-specific behaviour (triggers, timestamptz) is exercised by
``test_postgres_triggers.py`` when ``TEST_POSTGRES_URL`` is set, and by the
Docker Compose smoke test in CI.

A test module marked ``postgres`` asks for the stronger version of the same
thing: when a scratch PostgreSQL is provided through ``TEST_POSTGRES_URL``
(the CI job's ``postgres:16-alpine`` service), the *same* assertions run against
PostgreSQL, on the real migration chain. On SQLite the marker does nothing, so
one run keeps both backends' verdicts comparable (ADR-212). A too-narrow column
and a dialect-only JSON comparison have both shipped green here while failing on
the deployed NAS (ADR-064/ADR-186), which is why "the tests pass" has to mean
"on both".

The environment is pinned here, *before* any application module is imported,
so the suite is hermetic: it must not depend on whatever happens to be in the
developer's shell or ``.env``. Without this, a machine whose default market
data provider is ``yahoo_finance`` makes unrelated tests reach the network.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("MARKET_DATA_PROVIDER", "synthetic")
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("APP_ENVIRONMENT", "test")
# The research pipeline runs inline here: the suite has no broker and no worker, and
# the inline path is the same prepare/execute pair the queue drives. The async
# lifecycle has its own tests, which turn this back on and watch the seam instead.
os.environ.setdefault("AI_RESEARCH_ASYNC", "false")
# Same reasoning for backtests: the request executes the run inline, which is what almost
# every test asserts. test_backtest_status.py switches it on to watch the handover.
os.environ.setdefault("BACKTEST_ASYNC", "false")

import datetime as dt  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.db import Base, get_db  # noqa: E402
from app.domain import models as _models  # noqa: E402, F401  (register all tables)

#: The scratch PostgreSQL of the CI job, when it is offered at all. Read after the
#: imports because it only exists in the job that runs the ``postgres`` tests.
POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: when TEST_POSTGRES_URL is set, run this test against that scratch "
        "PostgreSQL instead of in-memory SQLite (same assertions, both backends)",
    )


def _reset_schema(engine) -> None:
    """Leave the scratch database looking like base, whatever a previous step did.

    The job's other PostgreSQL module (``test_postgres_triggers.py``) downgrades
    the same database when it finishes, but CI run 38159825772 showed the leftover
    can be inconsistent: the version said ``0003_fix_triggers_json`` while 0004's
    ``container_resource_samples`` was still there, and then ``upgrade head`` dies
    on ``DuplicateTable`` instead of migrating. So the schema is dropped outright
    rather than trusted, and this step no longer depends on the step before it.

    The second branch is because this fixture is backend-agnostic on purpose: it
    is what lets the same test module run on SQLite or on PostgreSQL.

    PostgreSQL drops the schema rather than the tables the models declare: the
    leftover above was a table no model knows about (it only exists in the
    migration chain), so a metadata-driven drop cannot see it and the upgrade
    would fail again.
    """

    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        return

    from sqlalchemy import MetaData

    metadata = MetaData()
    metadata.reflect(bind=engine)
    metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def _postgres_engine():
    """The scratch PostgreSQL, built by the real alembic chain, once per session.

    ``None`` (and therefore in-memory SQLite everywhere) whenever the CI job did
    not offer a database, which is every local run and the ordinary CI job.

    The URL is handed to the application's own engine constructor and to alembic
    the same way ``test_postgres_triggers.py`` does it: ``alembic/env.py`` reads
    ``settings.database_url``, not the environment, so the setting is what moves
    (ADR-098).
    """

    if not POSTGRES_URL:
        yield None
        return

    from app.core import db as db_module
    from app.core.config import settings

    original_url = settings.database_url
    settings.database_url = POSTGRES_URL  # type: ignore[assignment]
    try:
        from alembic.config import Config

        from alembic import command

        engine = db_module._build_engine(POSTGRES_URL)
        try:
            _reset_schema(engine)
            command.upgrade(Config("alembic.ini"), "head")
            yield engine
        finally:
            engine.dispose()
    finally:
        settings.database_url = original_url  # type: ignore[assignment]


def _empty_tables(session) -> None:
    """Leave the scratch PostgreSQL as empty as a fresh ``:memory:`` would be.

    SQLite hands every test its own database because the fixture creates one;
    PostgreSQL is migrated once per session, so the rows are deleted instead --
    in reverse dependency order, which is what ``sorted_tables`` already gives
    (parents first, so it is walked backwards). ``alembic_version`` is not in the
    metadata and is deliberately left alone.
    """

    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()


@pytest.fixture()
def db_session(request, tmp_path, _postgres_engine):
    """The database one test writes to: in-memory SQLite, or the scratch PostgreSQL.

    ``StaticPool`` is required for the SQLite path because Starlette runs sync
    endpoints in a worker thread: with the default pool each thread would get its
    *own* empty ``:memory:`` database.
    """

    if _postgres_engine is not None and request.node.get_closest_marker("postgres"):
        session = sessionmaker(bind=_postgres_engine, autoflush=False, expire_on_commit=False)()
        try:
            _empty_tables(session)
            yield session
        finally:
            session.rollback()
            session.close()
        return

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # SQLite ignores foreign keys unless they are switched on per connection, and
    # the production database is PostgreSQL: a suite that never enforces them
    # cannot see the difference between "this row is still referenced" and "this
    # row was orphaned". Turn the rules on so the tests fail the way production
    # does instead of silently accepting broken references.
    @event.listens_for(engine, "connect")
    def _sqlite_enforce_foreign_keys(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session, monkeypatch):
    from app.api.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def sample_bars() -> pd.DataFrame:
    """A deterministic 400-bar OHLCV series with an embedded breakout."""

    rng = np.random.default_rng(20240115)
    periods = 400
    index = pd.date_range("2023-01-02", periods=periods, freq="D", tz="UTC")
    steps = rng.normal(0.0003, 0.012, size=periods)
    close = 100.0 * np.exp(np.cumsum(steps))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0.0, 0.008, size=periods))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.0, 0.008, size=periods))
    volume = rng.integers(500_000, 3_000_000, size=periods).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    ).rename_axis("timestamp")


@pytest.fixture()
def flat_bars() -> pd.Series:
    """A perfectly flat price series: useful for boundary conditions."""

    index = pd.date_range("2024-01-01", periods=120, freq="D", tz="UTC")
    return pd.Series(100.0, index=index, name="close")


@pytest.fixture()
def utc_now() -> dt.datetime:
    return dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
