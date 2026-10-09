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
    version: StrategyVersion | None = None,
    bar_day: int = 2,
) -> StrategyVersion:
    """Add one signal under ``symbol``, with or without an outcome row.

    Pass ``version`` to put several signals on the *same* strategy version; the
    default gives every signal its own, which is what the un-scoped tests want.
    Signals on one version must differ in ``bar_day``: the table keeps one row
    per (version, asset, timeframe, bar_timestamp) — ``uq_signal_event``.
    """

    _SEQ["n"] += 1
    tag = _SEQ["n"]
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        asset = Asset(symbol=symbol, asset_class="stock")
        db.add(asset)
        db.flush()
    if version is None:
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
        bar_timestamp=dt.datetime(2026, 1, bar_day, tzinfo=dt.UTC),
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
    return version


def _version(db) -> StrategyVersion:
    """One strategy version that several signals can share."""

    _SEQ["n"] += 1
    tag = _SEQ["n"]
    strategy = Strategy(name=f"V-{tag}", slug=f"v-{tag}")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="v" * 64
    )
    db.add(version)
    db.commit()
    return version


def test_outcome_summary_groups(client, db_session) -> None:
    _signal(db_session, symbol="A1", state="BUY", direction="LONG", pnl=2.0)
    _signal(db_session, symbol="A2", state="BUY", direction="LONG", pnl=-1.0)
    _signal(db_session, symbol="A3", state="SELL", direction="FLAT", pnl=0.5)

    body = client.get("/api/v1/signals/outcome-summary").json()
    assert body["symbol"] is None
    # No version asked for -> the response says so instead of inventing a scope (ADR-201).
    assert body["strategy_version_id"] is None
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


def test_the_summary_can_be_narrowed_to_one_strategy_version(client, db_session) -> None:
    """A page pointed at one version must not read the portfolio-wide win rate."""

    first = _version(db_session)
    second = _version(db_session)
    _signal(db_session, symbol="AAA", pnl=1.0, version=first, bar_day=2)
    _signal(db_session, symbol="AAA", pnl=3.0, version=first, bar_day=3)
    _signal(db_session, symbol="AAA", with_outcome=False, version=first, bar_day=4)
    _signal(db_session, symbol="AAA", pnl=-5.0, version=second)

    whole = client.get("/api/v1/signals/outcome-summary").json()
    assert (whole["signals"], whole["decided"], whole["undecided"]) == (4, 3, 1)
    assert whole["groups"]["ALL"]["total_pnl_pct"] == -1.0

    scoped = client.get(f"/api/v1/signals/outcome-summary?strategy_version_id={first.id}").json()
    # The scope is echoed next to the numbers, so they cannot be mistaken for global ones.
    assert scoped["strategy_version_id"] == first.id
    assert (scoped["signals"], scoped["decided"], scoped["undecided"]) == (3, 2, 1)
    assert scoped["groups"]["ALL"]["count"] == 2
    assert scoped["groups"]["ALL"]["total_pnl_pct"] == 4.0
    assert scoped["groups"]["ALL"]["win_rate"] == 1.0

    other = client.get(f"/api/v1/signals/outcome-summary?strategy_version_id={second.id}").json()
    assert (other["signals"], other["decided"], other["undecided"]) == (1, 1, 0)
    assert other["groups"]["ALL"]["total_pnl_pct"] == -5.0
    assert other["groups"]["direction:LONG"]["count"] == 1
    # Each scope only ever holds its own version's signals: a leak would show up as a
    # second ``strategy:`` bucket (every version here has its own strategy).
    assert len([k for k in scoped["groups"] if k.startswith("strategy:")]) == 1
    assert len([k for k in other["groups"] if k.startswith("strategy:")]) == 1

    # Version + symbol compose: both filters apply.
    both = client.get(
        f"/api/v1/signals/outcome-summary?symbol=AAA&strategy_version_id={second.id}"
    ).json()
    assert (both["symbol"], both["strategy_version_id"]) == ("AAA", second.id)
    assert both["decided"] == 1


def test_a_version_with_no_signals_is_an_empty_scope_not_a_global_average(
    client, db_session
) -> None:
    _signal(db_session, symbol="AAA", pnl=4.0)

    body = client.get("/api/v1/signals/outcome-summary?strategy_version_id=999999").json()
    assert body["strategy_version_id"] == 999999
    assert (body["signals"], body["decided"], body["undecided"]) == (0, 0, 0)
    assert body["groups"] == {}


def test_the_outcome_list_can_be_narrowed_to_one_strategy_version(client, db_session) -> None:
    first = _version(db_session)
    second = _version(db_session)
    _signal(db_session, symbol="AAA", pnl=1.0, version=first, bar_day=2)
    _signal(db_session, symbol="AAA", pnl=2.0, version=second, bar_day=2)

    rows = client.get(f"/api/v1/signals/outcomes?strategy_version_id={first.id}").json()
    assert [row["pnl_pct"] for row in rows] == [1.0]
    assert (
        client.get(f"/api/v1/signals/outcomes?strategy_version_id={second.id}").json()[0]["pnl_pct"]
        == 2.0
    )
    # A version with no outcomes is empty, and the un-scoped list still sees both.
    assert client.get("/api/v1/signals/outcomes?strategy_version_id=999999").json() == []
    assert len(client.get("/api/v1/signals/outcomes").json()) == 2
