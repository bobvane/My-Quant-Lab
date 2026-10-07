"""Shared pytest fixtures.

Tests run against an in-memory SQLite database so the suite needs no external
services. PostgreSQL-specific behaviour (triggers, timestamptz) is exercised by
``test_postgres_triggers.py`` when ``TEST_POSTGRES_URL`` is set, and by the
Docker Compose smoke test in CI.

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


@pytest.fixture()
def db_session(tmp_path):
    """In-memory SQLite shared across threads.

    ``StaticPool`` is required because Starlette runs sync endpoints in a worker
    thread: with the default pool each thread would get its *own* empty
    ``:memory:`` database.
    """

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
