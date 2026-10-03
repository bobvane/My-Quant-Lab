"""WAIT state + feature-snapshot evidence (docs/09 §1/§5).

Before this, WAIT was defined but never produced, and the FeatureSnapshot table
was never written. These tests pin both: a partially-matched ``all`` entry group
yields WAIT on the latest bar, and every persisted signal stores the exact
feature row it came from.
"""

from __future__ import annotations

import pandas as pd

from app.domain.models import FeatureSnapshot
from app.simulation.signal_engine import scan_series
from app.strategies.dsl import StrategySpec
from app.strategies.executor import run_strategy

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "ws", "name": "WS", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {
        "long": {
            "all": [
                {"op": "gt", "left": "close", "right": "ema20"},
                {"op": "gt", "left": "close", "right": "ema50"},
            ]
        }
    },
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

# ``close < ema20`` is not a recommendation here, it is the comparison that holds
# on the last bar of the shared ``sample_bars`` fixture. The persistence tests
# need an intent that is genuinely fresh on the latest closed bar: under ADR-115
# an exit is an event (this fixture's decline started long before its last bar,
# so there is nothing to persist) and a stale level is not a signal either. The
# exit is the entry's inverse, so it cannot have just fired on that same bar.
_DSL_FIRES_ON_LATEST = {
    "schema_version": "1.0",
    "strategy": {"id": "ws-latest", "name": "WS-latest", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _frame(close: float, ema20: float, ema50: float) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    return pd.DataFrame(
        {"close": [close] * 5, "ema20": [ema20] * 5, "ema50": [ema50] * 5}, index=index
    )


def test_partial_entry_all_group_yields_wait() -> None:
    spec = StrategySpec.model_validate(_DSL)
    # close > ema20 holds, close > ema50 does not → partial match → WAIT.
    _out, intent = run_strategy(spec, _frame(close=10.0, ema20=9.0, ema50=11.0))
    assert intent["state"] == "WAIT"
    assert intent["bar_time"] is not None
    assert intent["triggered_rules"]  # the satisfied rule is recorded as evidence


def test_full_entry_yields_buy() -> None:
    spec = StrategySpec.model_validate(_DSL)
    _out, intent = run_strategy(spec, _frame(close=12.0, ema20=9.0, ema50=11.0))
    assert intent["state"] == "BUY"


def test_no_match_yields_no_signal() -> None:
    spec = StrategySpec.model_validate(_DSL)
    # close == ema20: entry gt fails and the exit (close < ema20) also fails.
    _out, intent = run_strategy(spec, _frame(close=9.0, ema20=9.0, ema50=11.0))
    assert intent["state"] == "NO_SIGNAL"
    assert intent["bar_time"] is None


def _seed_series(db, bars):
    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import (
        Asset,
        MarketDataSeries,
        MarketDataSource,
        Strategy,
        StrategyVersion,
    )

    strategy = Strategy(name="WaitSnap", slug="wait-snap")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json=_DSL_FIRES_ON_LATEST,
        immutable_hash="w" * 64,
        validation_status="valid",
        is_current=True,
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol="WS", asset_class="stock")
    db.add(asset)
    db.flush()
    source = MarketDataSource(name="ws-src", base_url="x")
    db.add(source)
    db.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db.add(series)
    db.flush()
    upsert_bars(db, series, frame_to_bars(bars))
    db.commit()
    return version, series


def test_signal_persists_feature_snapshot_evidence(db_session, client, sample_bars) -> None:
    version, series = _seed_series(db_session, sample_bars)
    signal = scan_series(db_session, version, series)
    # ``scan_series`` returns None when the latest closed bar holds no fresh
    # event; the fixture's entry does hold, so a None here means the fixture or
    # the freshness rule changed (ADR-115).
    assert signal is not None
    assert signal.state == "BUY"
    assert signal.closes_direction is None

    snapshot = db_session.query(FeatureSnapshot).one()
    assert snapshot.input_hash == signal.feature_snapshot_hash
    assert snapshot.values_json  # a real feature row, not an empty dict
    assert snapshot.bar_timestamp == signal.bar_timestamp

    latest = client.get(f"/api/v1/feature-snapshots/{series.id}/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["input_hash"] == signal.feature_snapshot_hash
    assert "close" in body["values"]


def test_feature_snapshot_endpoint_validates_series(client) -> None:
    assert client.get("/api/v1/feature-snapshots/9999/latest").status_code == 404


def test_signal_evidence_endpoint(client, db_session, sample_bars) -> None:
    version, series = _seed_series(db_session, sample_bars)
    signal = scan_series(db_session, version, series)
    assert signal is not None

    body = client.get(f"/api/v1/signals/{signal.id}/evidence").json()
    assert body["signal_id"] == signal.id
    assert body["feature_snapshot_hash"] == signal.feature_snapshot_hash
    assert body["feature_snapshot"] is not None
    assert body["feature_snapshot"]["input_hash"] == signal.feature_snapshot_hash
    assert "close" in body["feature_snapshot"]["values"]

    assert client.get("/api/v1/signals/9999/evidence").status_code == 404
