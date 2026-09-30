"""Signal outcome evaluation tests."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from app.simulation.outcome_evaluator import evaluate_pending_outcomes


def _seed_signal_with_bars(db_session, *, direction: str = "LONG"):
    """Create a signal + enough bars for evaluation, return (signal_id, bars)."""

    import numpy as np

    from app.domain.models import (
        Asset,
        MarketDataSeries,
        MarketDataSource,
        Signal,
        Strategy,
        StrategyVersion,
    )

    strategy = Strategy(name="OutcomeTest", slug="outcome-test")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="x" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="OUTCOME", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="outcome-src", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    bar_ts = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=bar_ts,
        state="BUY",
        direction=direction,
        price_reference=100.0,
        feature_snapshot_hash="h" * 64,
        data_source="series:1/v1",
    )
    db_session.add(signal)
    db_session.flush()

    # Build bars: signal close = 100, then rises to 110, dips to 95, closes at 108 after 10 bars
    closes = np.concatenate([[100.0], np.linspace(100, 108, 10)])
    highs = closes + 2
    lows = closes - 3  # dips to 97 early on (MAE)
    index = pd.date_range(bar_ts, periods=11, freq="D", tz="UTC")
    bars = pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": 1000.0},
        index=index,
    ).rename_axis("timestamp")

    return signal.id, bars, series


def test_outcome_evaluates_after_enough_bars(db_session) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars

    signal_id, bars, series = _seed_signal_with_bars(db_session)
    upsert_bars(db_session, series, frame_to_bars(bars))
    db_session.commit()

    result = evaluate_pending_outcomes(db_session, bars_after=10)
    assert result["evaluated"] == 1
    assert result["insufficient_data"] == 0

    from app.domain.models import SignalOutcome

    outcome = db_session.query(SignalOutcome).one()
    assert outcome.pnl_pct is not None
    assert outcome.pnl_pct > 0  # price rose
    assert outcome.mae is not None and outcome.mae >= 0
    assert outcome.mfe is not None and outcome.mfe >= 0
    assert outcome.outcome_state == "profitable"


def test_second_call_is_idempotent(db_session) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import SignalOutcome

    signal_id, bars, series = _seed_signal_with_bars(db_session)
    upsert_bars(db_session, series, frame_to_bars(bars))
    db_session.commit()

    evaluate_pending_outcomes(db_session, bars_after=10)
    result = evaluate_pending_outcomes(db_session, bars_after=10)
    assert result["evaluated"] == 0  # already evaluated, not double-counted
    assert db_session.query(SignalOutcome).count() == 1


def test_short_direction_flips_pnl(db_session) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import SignalOutcome

    signal_id, bars, series = _seed_signal_with_bars(db_session, direction="SHORT")
    upsert_bars(db_session, series, frame_to_bars(bars))
    db_session.commit()

    evaluate_pending_outcomes(db_session, bars_after=10)
    outcome = db_session.query(SignalOutcome).one()
    # Price rose but direction is SHORT → negative pnl
    assert outcome.pnl_pct < 0
    assert outcome.outcome_state == "unprofitable"
