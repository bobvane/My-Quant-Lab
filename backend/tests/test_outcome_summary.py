"""Aggregated signal-outcome statistics (docs/09 §8)."""

from __future__ import annotations

import datetime as dt

from app.domain.models import Asset, Signal, SignalOutcome, Strategy, StrategyVersion

_SEQ = {"n": 0}


def _seed(db, *, state: str, direction: str, pnl: float) -> None:
    _SEQ["n"] += 1
    tag = _SEQ["n"]
    strategy = Strategy(name=f"S-{state}-{direction}-{tag}", slug=f"s-{tag}")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="o" * 64
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol=f"A{state}{direction}{tag}", asset_class="stock")
    db.add(asset)
    db.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state=state,
        direction=direction,
        price_reference=100.0,
        feature_snapshot_hash="o" * 64,
        data_source="test",
    )
    db.add(signal)
    db.flush()
    db.add(SignalOutcome(signal_id=signal.id, outcome_state="closed", pnl_pct=pnl))
    db.commit()


def test_outcome_summary_groups(client, db_session) -> None:
    _seed(db_session, state="BUY", direction="LONG", pnl=2.0)
    _seed(db_session, state="BUY", direction="LONG", pnl=-1.0)
    _seed(db_session, state="SELL", direction="FLAT", pnl=0.5)

    body = client.get("/api/v1/signals/outcome-summary").json()
    assert body["evaluated"] == 3
    all_group = body["groups"]["ALL"]
    assert all_group["count"] == 3
    assert all_group["win_rate"] == round(2 / 3, 4)
    assert all_group["total_pnl_pct"] == 1.5
    assert body["groups"]["state:BUY"]["count"] == 2
    assert body["groups"]["direction:FLAT"]["count"] == 1
