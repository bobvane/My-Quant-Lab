"""Shared pytest fixtures.

Tests run against an in-memory SQLite database so the suite needs no external
services. PostgreSQL-specific behaviour (JSONB, partitioning) is exercised in CI
through the Docker Compose smoke test instead.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain import models as _models  # noqa: F401  (register all tables)


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
