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
    # docs/11: the outcome records when the window opened and closed.
    assert outcome.entry_time is not None
    assert outcome.exit_time is not None
    assert outcome.exit_time > outcome.entry_time


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


# ---- the on-demand trigger (ADR-202) ----------------------------------------
#
# Backfilling is the beat task's job, but a single-container run has no scheduler,
# so the same idempotent function is reachable over HTTP. These tests pin that the
# endpoint *is* that function — not a second implementation with its own rules.


def _pending_signal_without_a_series(db_session) -> tuple[int, int]:
    """One pending entry signal on its own version, with no candle series at all."""

    from app.domain.models import Asset, Signal, Strategy, StrategyVersion

    strategy = Strategy(name="OutcomeNoSeries", slug="outcome-no-series")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="y" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="NOSERIES", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=100.0,
        feature_snapshot_hash="n" * 64,
        data_source="series:0/v1",
    )
    db_session.add(signal)
    db_session.commit()
    return version.id, signal.id


def test_the_endpoint_backfills_what_the_scheduler_would_have(client, db_session) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars

    _signal_id, bars, series = _seed_signal_with_bars(db_session)
    upsert_bars(db_session, series, frame_to_bars(bars))
    db_session.commit()

    before = client.get("/api/v1/signals/outcome-summary").json()
    assert before["decided"] == 0

    body = client.post("/api/v1/signals/outcomes/evaluate").json()
    assert body["evaluated"] == 1
    assert body["insufficient_data"] == 0
    # The window is the constant the summary publishes, not a caller's choice.
    assert body["bars_after"] == 10
    assert body["strategy_version_id"] is None

    after = client.get("/api/v1/signals/outcome-summary").json()
    assert after["signals"] == 1
    assert after["decided"] == 1
    assert after["undecided"] == 0


def test_the_endpoint_only_touches_the_version_it_names(client, db_session) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import Signal

    signal_id, bars, series = _seed_signal_with_bars(db_session)
    upsert_bars(db_session, series, frame_to_bars(bars))
    db_session.commit()
    scoped_version_id = db_session.get(Signal, signal_id).strategy_version_id
    other_version_id, _other_signal_id = _pending_signal_without_a_series(db_session)

    scoped = client.post(
        f"/api/v1/signals/outcomes/evaluate?strategy_version_id={scoped_version_id}"
    ).json()
    assert scoped["strategy_version_id"] == scoped_version_id
    assert scoped["evaluated"] == 1
    # The other version's signal is not in this scope, so it is not counted as work
    # at all — neither evaluated nor skipped.
    assert scoped["skipped"] == 0

    other = client.post(
        f"/api/v1/signals/outcomes/evaluate?strategy_version_id={other_version_id}"
    ).json()
    assert other["strategy_version_id"] == other_version_id
    assert other["evaluated"] == 0
    # And it reports the real reason rather than an unexplained zero.
    assert other["skipped"] == 1


def test_the_endpoint_says_a_second_call_has_nothing_to_do(client, db_session) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import SignalOutcome

    _signal_id, bars, series = _seed_signal_with_bars(db_session)
    upsert_bars(db_session, series, frame_to_bars(bars))
    db_session.commit()

    first = client.post("/api/v1/signals/outcomes/evaluate").json()
    second = client.post("/api/v1/signals/outcomes/evaluate").json()
    assert first["evaluated"] == 1
    assert second["evaluated"] == 0
    assert db_session.query(SignalOutcome).count() == 1
