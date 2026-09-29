"""PostgreSQL-only regression tests (skipped without TEST_POSTGRES_URL).

Background: the 0002 immutability triggers are installed on PostgreSQL only,
so the SQLite suite could not see them. ``IS DISTINCT FROM`` on ``json``
columns — which PostgreSQL rejects with
``operator does not exist: json = json`` — shipped and broke *every*
strategy-version creation in production with HTTP 500, including the very
first version (the ``is_current`` flip itself fires the trigger).

These tests run the REAL migration chain against a REAL PostgreSQL and
exercise the exact service-layer paths that failed. In CI the backend job
provides a postgres service and sets TEST_POSTGRES_URL.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("TEST_POSTGRES_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL not set")

DROP_STRATEGY_FN = "DROP FUNCTION IF EXISTS quantlab_reject_strategy_version_mutation() CASCADE"
DROP_RESULT_FN = "DROP FUNCTION IF EXISTS quantlab_reject_result_mutation() CASCADE"

DSL: dict[str, Any] = {
    "schema_version": "1.0",
    "strategy": {"id": "pg-test", "name": "PG Trigger Test", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


@pytest.fixture(scope="module")
def pg_session_factory():
    """Migrate a scratch database with the real alembic chain, then hand out sessions."""
    if not PG_URL:
        pytest.skip("TEST_POSTGRES_URL not set")

    from app.core import db as db_module
    from app.core.config import settings

    original_url = settings.database_url
    settings.database_url = PG_URL  # type: ignore[assignment]
    try:
        from alembic.config import Config

        from alembic import command

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        # Build sessions with the REAL application engine constructor so the
        # test guards the app's own pool/timezone/timeout configuration.
        engine = db_module._build_engine(PG_URL)
        try:
            yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        finally:
            engine.dispose()
            # Leave the database clean for the next run regardless of outcome.
            try:
                command.downgrade(cfg, "base")
            except Exception:
                _force_clean(PG_URL)
    finally:
        settings.database_url = original_url  # type: ignore[assignment]


def _force_clean(pg_url: str) -> None:
    """Drop everything the migrations created (fallback if downgrade fails)."""
    from sqlalchemy import text

    from app.core.db import Base

    drop_engine = create_engine(pg_url, future=True)
    try:
        with drop_engine.begin() as conn:
            conn.execute(text(DROP_STRATEGY_FN))
            conn.execute(text(DROP_RESULT_FN))
        Base.metadata.drop_all(drop_engine)
        conn = drop_engine.connect()
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()
        conn.close()
    finally:
        drop_engine.dispose()


@pytest.fixture()
def pg_db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@needs_pg
def test_create_two_versions_flips_is_current(pg_db) -> None:
    """The exact CI failure: creating a version must not 500 on the trigger."""
    from app.data.strategy_service import create_strategy_version
    from app.domain.models import Strategy, StrategyVersion

    strategy = Strategy(name="PG Versions", slug="pg-versions")
    pg_db.add(strategy)
    pg_db.flush()

    v1 = create_strategy_version(pg_db, strategy, version="1.0.0", dsl=DSL)
    assert v1.is_current is True

    v2 = create_strategy_version(pg_db, strategy, version="1.1.0", dsl=DSL)
    pg_db.refresh(v1)
    assert v1.is_current is False
    assert v2.is_current is True

    current = pg_db.query(StrategyVersion).filter_by(strategy_id=strategy.id, is_current=True).all()
    assert [row.version for row in current] == ["1.1.0"]


@needs_pg
def test_direct_dsl_mutation_is_rejected(pg_db) -> None:
    """Guarded columns still reject direct mutation; unguarded ones pass."""
    from sqlalchemy import update

    from app.data.strategy_service import create_strategy_version
    from app.domain.models import Strategy, StrategyVersion

    strategy = Strategy(name="PG Guard", slug="pg-guard")
    pg_db.add(strategy)
    pg_db.flush()
    version = create_strategy_version(pg_db, strategy, version="1.0.0", dsl=DSL)

    with pytest.raises(ProgrammingError, match="immutable"):
        pg_db.execute(
            update(StrategyVersion)
            .where(StrategyVersion.id == version.id)
            .values(dsl_json={"tampered": True})
        )
        pg_db.flush()
    pg_db.rollback()

    with pytest.raises(ProgrammingError, match="immutable"):
        pg_db.execute(
            update(StrategyVersion).where(StrategyVersion.id == version.id).values(version="9.9.9")
        )
        pg_db.flush()
    pg_db.rollback()

    # The service layer relies on flipping is_current: it must keep working.
    pg_db.execute(
        update(StrategyVersion).where(StrategyVersion.id == version.id).values(is_current=False)
    )
    pg_db.flush()
    pg_db.refresh(version)
    assert version.is_current is False


@needs_pg
def test_backtest_result_mutation_is_rejected(pg_db) -> None:
    """The backtest-results trigger had the same json-comparison bug."""
    from sqlalchemy import update

    from app.data.strategy_service import create_strategy_version
    from app.domain.models import (
        Asset,
        BacktestResult,
        BacktestRun,
        MarketDataSeries,
        MarketDataSource,
        Strategy,
    )

    strategy = Strategy(name="PG Result", slug="pg-result")
    pg_db.add(strategy)
    pg_db.flush()
    version = create_strategy_version(pg_db, strategy, version="1.0.0", dsl=DSL)

    asset = Asset(symbol="PGT", asset_class="stock")
    pg_db.add(asset)
    pg_db.flush()
    source = MarketDataSource(name="pg-test-src", provider_type="rest_api", base_url="x")
    pg_db.add(source)
    pg_db.flush()
    series = MarketDataSeries(
        asset_id=asset.id, timeframe="1d", source_id=source.id, dataset_version="v1"
    )
    pg_db.add(series)
    pg_db.flush()

    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        parameters_json={},
        execution_model_json={},
        dataset_hash="abc",
        status="completed",
    )
    pg_db.add(run)
    pg_db.flush()
    result = BacktestResult(
        backtest_run_id=run.id,
        summary_json={"total_return": 0.1},
        equity_curve_json=[],
        metrics_json={},
        result_hash="deadbeef",
    )
    pg_db.add(result)
    pg_db.flush()

    with pytest.raises(ProgrammingError, match="immutable"):
        pg_db.execute(
            update(BacktestResult)
            .where(BacktestResult.id == result.id)
            .values(summary_json={"total_return": 999.0})
        )
        pg_db.flush()
    pg_db.rollback()

    # Unguarded tables keep working.
    pg_db.execute(update(BacktestRun).where(BacktestRun.id == run.id).values(status="archived"))
    pg_db.flush()


@needs_pg
def test_timestamps_come_back_utc(pg_db) -> None:
    """Session timezone is pinned to UTC: stored instants read back as UTC."""
    from app.domain.models import Strategy

    strategy = Strategy(name="PG TZ", slug="pg-tz")
    pg_db.add(strategy)
    pg_db.flush()
    pg_db.refresh(strategy)
    assert strategy.created_at.tzinfo is not None
    assert strategy.created_at.utcoffset() == dt.timedelta(0)
