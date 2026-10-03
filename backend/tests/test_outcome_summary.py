"""Aggregated signal-outcome statistics (docs/12 Signals)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.domain.models import Asset, Signal, SignalOutcome, Strategy, StrategyVersion

_SEQ = {"n": 0}


def _signal(
    db,
    *,
    symbol: str,
    state: str = "BUY",
    direction: str = "LONG",
    pnl: float | None = 1.0,
    with_outcome: bool = True,
) -> None:
    """Add one signal under ``symbol``, with or without an outcome row."""

    _SEQ["n"] += 1
    tag = _SEQ["n"]
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        asset = Asset(symbol=symbol, asset_class="stock")
        db.add(asset)
        db.flush()
    strategy = Strategy(name=f"S-{symbol}-{tag}", slug=f"s-{symbol.lower()}-{tag}")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="o" * 64
    )
    db.add(version)
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
    if with_outcome:
        db.add(SignalOutcome(signal_id=signal.id, outcome_state="closed", pnl_pct=pnl))
    db.commit()


def test_outcome_summary_groups(client, db_session) -> None:
    _signal(db_session, symbol="A1", state="BUY", direction="LONG", pnl=2.0)
    _signal(db_session, symbol="A2", state="BUY", direction="LONG", pnl=-1.0)
    _signal(db_session, symbol="A3", state="SELL", direction="FLAT", pnl=0.5)

    body = client.get("/api/v1/signals/outcome-summary").json()
    assert body["symbol"] is None
    assert body["signals"] == 3
    assert body["decided"] == 3
    assert body["undecided"] == 0
    assert body["bars_after"] == 10
    all_group = body["groups"]["ALL"]
    assert all_group["count"] == 3
    assert all_group["win_rate"] == round(2 / 3, 4)
    assert all_group["total_pnl_pct"] == 1.5
    assert body["groups"]["state:BUY"]["count"] == 2
    assert body["groups"]["direction:FLAT"]["count"] == 1
    # Each signal belongs to its own seeded strategy, so 3 strategy groups exist.
    assert len([k for k in body["groups"] if k.startswith("strategy:")]) == 3


def test_the_summary_states_the_denominator_of_its_win_rate(client, db_session) -> None:
    for pnl in (2.0, -1.0, 0.5):
        _signal(db_session, symbol=f"DECIDED-{pnl}", pnl=pnl)
    for n in range(4):
        _signal(db_session, symbol=f"WAITING-{n}", with_outcome=False)

    body = client.get("/api/v1/signals/outcome-summary").json()
    assert body["signals"] == 7
    assert body["decided"] == 3
    assert body["undecided"] == 4
    # The win rate is computed over decided signals only — and says so.
    assert body["decided"] == body["groups"]["ALL"]["count"]
    assert body["groups"]["ALL"]["win_rate"] == round(2 / 3, 4)
    assert body["decided"] + body["undecided"] == body["signals"]


def test_the_summary_only_counts_the_symbol_it_names(client, db_session) -> None:
    _signal(db_session, symbol="AAA", pnl=1.0)
    _signal(db_session, symbol="AAA", pnl=2.0)
    _signal(db_session, symbol="BBB", pnl=-3.0)
    _signal(db_session, symbol="BBB", with_outcome=False)

    scoped = client.get("/api/v1/signals/outcome-summary?symbol=AAA").json()
    assert scoped["symbol"] == "AAA"
    assert (scoped["signals"], scoped["decided"], scoped["undecided"]) == (2, 2, 0)
    assert scoped["groups"]["ALL"]["count"] == 2

    other = client.get("/api/v1/signals/outcome-summary?symbol=BBB").json()
    assert other["symbol"] == "BBB"
    assert (other["signals"], other["decided"], other["undecided"]) == (2, 1, 1)
    assert other["groups"]["ALL"]["count"] == 1

    whole = client.get("/api/v1/signals/outcome-summary").json()
    assert (whole["signals"], whole["decided"], whole["undecided"]) == (4, 3, 1)


def test_an_unknown_symbol_is_an_empty_scope_not_a_global_average(client, db_session) -> None:
    _signal(db_session, symbol="AAA", pnl=1.0)

    body = client.get("/api/v1/signals/outcome-summary?symbol=NOPE").json()
    assert body["symbol"] == "NOPE"
    assert (body["signals"], body["decided"], body["undecided"]) == (0, 0, 0)
    assert body["groups"] == {}


def test_an_outcome_without_a_pnl_is_not_a_decided_signal(client, db_session) -> None:
    _signal(db_session, symbol="AAA", pnl=1.0)
    _signal(db_session, symbol="BBB", pnl=None)  # outcome row exists, no usable number

    body = client.get("/api/v1/signals/outcome-summary").json()
    assert (body["signals"], body["decided"], body["undecided"]) == (2, 1, 1)
    assert body["groups"]["ALL"]["count"] == body["decided"]


def test_list_outcomes_endpoint(client, db_session) -> None:
    _signal(db_session, symbol="AAA", pnl=1.25)
    rows = client.get("/api/v1/signals/outcomes").json()
    assert len(rows) == 1
    assert rows[0]["pnl_pct"] == 1.25
    assert rows[0]["outcome_state"] == "closed"

    # Unknown symbol -> empty (not a crash).
    assert client.get("/api/v1/signals/outcomes?symbol=NOPE").json() == []
