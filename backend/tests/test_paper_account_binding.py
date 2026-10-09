"""ADR-181: a paper account opened from a backtest, marked, and sized by hand.

Three promises are checked here, and each of them used to be missing:

* the account remembers the strategy *version* and the exact parameters of the backtest
  it came from, so its result can be compared with the run that motivated it;
* the account shows what the open positions are worth -- market value, unrealised P&L and
  total equity -- instead of only cash and realised P&L, because cash alone reads as a
  total loss the moment a purchase has been paid for (ADR-124);
* a manual order can name a quantity or a notional amount, since "all in" is not a size a
  person can express (ADR-030 keeps every existing refusal, including the cash guard).

Everything is computed from the account's own tables plus stored public bars: no external
service is contacted and no price is invented (ADR-006, ADR-007).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_DOWN, Decimal

import pandas as pd
import pytest
from sqlalchemy import select

from app.data.market_data_repo import frame_to_bars, upsert_bars
from app.domain.models import (
    Asset,
    AuditLog,
    BacktestRun,
    ExperimentResult,
    MarketDataSeries,
    MarketDataSource,
    PaperAccount,
    PaperPosition,
    PaperTrade,
    Signal,
    Strategy,
    StrategyExperiment,
    StrategyVersion,
)

#: Every series here starts at this instant, so a mark's timestamp is a fixed fact.
BAR_START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

#: The engine floors a computed size at the quantity column's scale (Numeric(24, 10)).
QUANTITY_SCALE = Decimal("1E-10")


def _seed_strategy(db, *, slug: str) -> tuple[Strategy, StrategyVersion]:
    strategy = Strategy(name=f"Strategy {slug}", slug=slug)
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={},
        immutable_hash="b" * 64,
        validation_status="valid",
    )
    db.add(version)
    db.flush()
    return strategy, version


def _seed_asset(db, *, symbol: str) -> Asset:
    asset = Asset(symbol=symbol, asset_class="stock")
    db.add(asset)
    db.flush()
    return asset


def _seed_bars(db, asset: Asset, closes: list[float], *, closed: bool = True) -> MarketDataSeries:
    """Store one daily bar per close; ``closed=False`` persists still-forming bars."""

    source = MarketDataSource(name=f"src-{asset.symbol}", base_url="test")
    db.add(source)
    db.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db.add(series)
    db.flush()
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [1_000.0] * len(closes),
            "is_closed": [closed] * len(closes),
        },
        index=pd.date_range(start=BAR_START, periods=len(closes), freq="D"),
    )
    upsert_bars(db, series, frame_to_bars(frame))
    db.flush()
    return series


def _seed_run(
    db,
    *,
    version: StrategyVersion,
    series: MarketDataSeries,
    status: str = "completed",
    parameters: dict | None = None,
) -> BacktestRun:
    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        dataset_hash="a" * 64,
        status=status,
        parameters_json={"fast": 10, "slow": 30} if parameters is None else parameters,
    )
    db.add(run)
    db.flush()
    return run


def _seed_experiment(
    db,
    *,
    version: StrategyVersion,
    run: BacktestRun | None = None,
    name: str = "Exp",
    kind: str = "backtest",
    parameters: dict | None = None,
) -> StrategyExperiment:
    """An experiment, with its run lineage recorded the way production records it.

    A `StrategyExperiment` has no `backtest_run_id` column: the link to a run lives on
    the `ExperimentResult` rows (ADR-054/055/183), so a run passed here becomes one.
    """

    experiment = StrategyExperiment(
        name=name,
        kind=kind,
        status="completed",
        strategy_version_id=version.id,
        parameters_json={"fast": 10, "slow": 30} if parameters is None else parameters,
        request_json={},
    )
    db.add(experiment)
    db.flush()
    if run is not None:
        db.add(
            ExperimentResult(
                experiment_id=experiment.id,
                kind="point",
                backtest_run_id=run.id,
                payload_json={},
            )
        )
        db.flush()
    return experiment


def _seed_account(db, *, cash: float = 10_000.0, name: str = "PA") -> PaperAccount:
    account = PaperAccount(name=name, initial_cash=cash, cash=cash)
    db.add(account)
    db.flush()
    return account


def _seed_signal(
    db, *, version: StrategyVersion, asset: Asset, state: str, price: float, bar: dt.datetime
) -> Signal:
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=bar,
        state=state,
        direction="LONG",
        price_reference=price,
        triggered_rules_json=[],
        feature_snapshot_hash="f" * 64,
        data_source="test",
    )
    db.add(signal)
    db.flush()
    return signal


def _create(client, **body) -> dict:
    response = client.post("/api/v1/paper/accounts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _execute(client, account_id: int, **body) -> dict:
    response = client.post(f"/api/v1/paper/accounts/{account_id}/execute", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _account_body(client, account_id: int) -> dict:
    response = client.get(f"/api/v1/paper/accounts/{account_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _positions(client, account_id: int) -> list[dict]:
    response = client.get(f"/api/v1/paper/accounts/{account_id}/positions")
    assert response.status_code == 200, response.text
    return response.json()


def _moment(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


# --------------------------------------------------------------------------- #
# The binding: a finished backtest is what the account is opened from
# --------------------------------------------------------------------------- #


def test_an_account_from_a_completed_backtest_carries_the_version_and_parameters(
    client, db_session
):
    strategy, version = _seed_strategy(db_session, slug="bt-binding")
    asset = _seed_asset(db_session, symbol="BTBIND")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series)
    db_session.commit()

    body = _create(client, name="From a backtest", initial_cash=5_000, backtest_run_id=run.id)

    assert body["backtest_run_id"] == run.id
    assert body["strategy_version_id"] == version.id
    # The legacy column keeps its meaning: it names the strategy that owns the version.
    assert body["strategy_id"] == strategy.id
    assert body["strategy_name"] == strategy.name
    assert body["parameters"] == {"fast": 10, "slow": 30}
    assert body["net_deposits"] == 5_000.0
    assert body["cash"] == 5_000.0

    stored = db_session.get(PaperAccount, body["id"])
    assert stored.parameters_json == {"fast": 10, "slow": 30}
    assert stored.backtest_run_id == run.id
    assert stored.strategy_version_id == version.id

    fetched = _account_body(client, body["id"])
    assert fetched["strategy_version_id"] == version.id
    assert fetched["strategy_name"] == strategy.name
    assert fetched["parameters"] == {"fast": 10, "slow": 30}


def test_caller_parameters_win_over_the_runs(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="bt-params")
    asset = _seed_asset(db_session, symbol="BTPARAM")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series)
    db_session.commit()

    body = _create(
        client,
        name="Overridden",
        initial_cash=1_000,
        backtest_run_id=run.id,
        parameters={"fast": 3},
    )

    assert body["parameters"] == {"fast": 3}
    assert body["backtest_run_id"] == run.id


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_a_run_that_did_not_complete_cannot_be_bound(client, db_session, status):
    _strategy, version = _seed_strategy(db_session, slug=f"bt-{status}")
    asset = _seed_asset(db_session, symbol=f"BT{status.upper()}")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series, status=status)
    db_session.commit()

    response = client.post(
        "/api/v1/paper/accounts",
        json={"name": "Too early", "initial_cash": 1_000, "backtest_run_id": run.id},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == f"backtest run {run.id} is '{status}', not 'completed'"


def test_binding_to_a_run_that_does_not_exist_is_refused(client) -> None:
    response = client.post(
        "/api/v1/paper/accounts",
        json={"name": "Missing run", "initial_cash": 1_000, "backtest_run_id": 999_999},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "backtest run 999999 not found"


def test_binding_to_a_version_that_does_not_exist_is_refused(client) -> None:
    response = client.post(
        "/api/v1/paper/accounts",
        json={"name": "Missing version", "initial_cash": 1_000, "strategy_version_id": 999_999},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "strategy version 999999 not found"


def test_a_version_from_another_strategy_is_refused(client, db_session):
    _owner, version = _seed_strategy(db_session, slug="bt-owner")
    other, _other_version = _seed_strategy(db_session, slug="bt-other")
    db_session.commit()

    response = client.post(
        "/api/v1/paper/accounts",
        json={
            "name": "Confused",
            "initial_cash": 1_000,
            "strategy_id": other.id,
            "strategy_version_id": version.id,
        },
    )

    assert response.status_code == 422
    assert "does not own strategy version" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The experiment binding: which record the user actually acted on (ADR-209)
# --------------------------------------------------------------------------- #


def test_an_account_opened_from_an_experiment_remembers_which_experiment(client, db_session):
    strategy, version = _seed_strategy(db_session, slug="exp-binding")
    asset = _seed_asset(db_session, symbol="EXPBIND")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series)
    experiment = _seed_experiment(db_session, version=version, run=run)
    db_session.commit()

    body = _create(
        client,
        name="From an experiment",
        initial_cash=7_000,
        backtest_run_id=run.id,
        experiment_id=experiment.id,
    )

    assert body["experiment_id"] == experiment.id
    assert body["backtest_run_id"] == run.id
    assert body["strategy_version_id"] == version.id
    assert body["strategy_id"] == strategy.id

    stored = db_session.get(PaperAccount, body["id"])
    assert stored.experiment_id == experiment.id
    # Recording the origin changed nothing about the origin itself.
    assert db_session.get(StrategyExperiment, experiment.id) is not None

    assert _account_body(client, body["id"])["experiment_id"] == experiment.id

    # The audit event names it too, so the provenance is not only in one place.
    audit = db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == "paper_account",
            AuditLog.entity_id == str(body["id"]),
        )
    ).one()
    assert audit.payload_json is not None
    assert audit.payload_json["experiment_id"] == experiment.id


def test_an_account_not_opened_from_an_experiment_says_so(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="exp-absent")
    asset = _seed_asset(db_session, symbol="EXPABSENT")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series)
    db_session.commit()

    body = _create(client, name="From a backtest only", initial_cash=1_000, backtest_run_id=run.id)

    # `null` is the honest answer, not a zero and not a guess.
    assert body["experiment_id"] is None


def test_an_experiment_binding_alone_carries_its_version_and_parameters(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="exp-only")
    asset = _seed_asset(db_session, symbol="EXPONLY")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series, parameters={"fast": 7, "slow": 21})
    experiment = _seed_experiment(
        db_session, version=version, run=run, parameters={"fast": 7, "slow": 21}
    )
    db_session.commit()

    body = _create(client, name="Experiment only", initial_cash=2_000, experiment_id=experiment.id)

    assert body["experiment_id"] == experiment.id
    assert body["strategy_version_id"] == version.id
    assert body["parameters"] == {"fast": 7, "slow": 21}
    # The experiment names a run, but the caller did not open the account from that run;
    # copying it here would claim a binding nobody asked for.
    assert body["backtest_run_id"] is None


def test_an_account_cannot_name_an_experiment_that_ran_a_different_backtest(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="exp-mismatch")
    asset = _seed_asset(db_session, symbol="EXPMISMATCH")
    series = _seed_bars(db_session, asset, [100.0])
    mine = _seed_run(db_session, version=version, series=series)
    other = _seed_run(db_session, version=version, series=series)
    experiment = _seed_experiment(db_session, version=version, run=mine)
    db_session.commit()

    response = client.post(
        "/api/v1/paper/accounts",
        json={
            "name": "Contradiction",
            "initial_cash": 1_000,
            "backtest_run_id": other.id,
            "experiment_id": experiment.id,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        f"experiment {experiment.id} ran backtest run {mine.id}, not {other.id}"
    )


def test_binding_to_an_experiment_that_does_not_exist_is_refused(client) -> None:
    response = client.post(
        "/api/v1/paper/accounts",
        json={"name": "Missing experiment", "initial_cash": 1_000, "experiment_id": 999_999},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "experiment 999999 not found"


def test_deleting_the_experiment_clears_the_reference_and_keeps_the_account(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="exp-delete")
    asset = _seed_asset(db_session, symbol="EXPDELETE")
    series = _seed_bars(db_session, asset, [100.0])
    run = _seed_run(db_session, version=version, series=series)
    experiment = _seed_experiment(db_session, version=version, run=run)
    db_session.commit()

    body = _create(
        client, name="Outlives its experiment", initial_cash=3_000, experiment_id=experiment.id
    )
    assert client.delete(f"/api/v1/experiments/{experiment.id}").status_code == 204

    fetched = _account_body(client, body["id"])
    # The account stands, with one fewer thing to point at -- never a dangling id.
    assert fetched["name"] == "Outlives its experiment"
    assert fetched["experiment_id"] is None

    db_session.expire_all()
    stored = db_session.get(PaperAccount, body["id"])
    assert stored is not None
    assert stored.experiment_id is None


# --------------------------------------------------------------------------- #
# Marks and totals: what the positions are worth right now
# --------------------------------------------------------------------------- #


def test_totals_mark_the_open_position_at_the_latest_closed_bar(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="totals")
    asset = _seed_asset(db_session, symbol="TOTALS")
    _seed_bars(db_session, asset, [100.0, 110.0])
    account = _seed_account(db_session, cash=10_000.0)
    # Ten units bought at 100: the 1_000 that paid for them is gone from cash, and only
    # the position itself still accounts for it.
    account.cash = Decimal("9000")
    db_session.add(
        PaperPosition(
            account_id=account.id,
            asset_id=asset.id,
            quantity=Decimal("10"),
            avg_cost=Decimal("100"),
            realized_pnl=Decimal("0"),
        )
    )
    db_session.commit()

    body = _account_body(client, account.id)

    assert body["cash"] == 9000.0
    assert body["net_deposits"] == 10_000.0
    assert body["market_value"] == 1100.0
    assert body["unrealized_pnl"] == 100.0
    assert body["total_equity"] == 10_100.0
    assert body["cash"] + body["market_value"] == body["total_equity"]
    assert body["realized_pnl"] == 0.0
    assert body["total_pnl"] == 100.0
    assert body["total_pnl_pct"] == pytest.approx(0.01)
    assert body["metric_notes"] == []

    listed = client.get("/api/v1/paper/accounts").json()
    assert listed[0]["total_equity"] == 10_100.0

    positions = _positions(client, account.id)
    assert len(positions) == 1
    row = positions[0]
    assert row["symbol"] == "TOTALS"
    assert row["quantity"] == 10.0
    assert row["mark_price"] == 110.0
    assert _moment(row["mark_time"]) == dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    assert row["mark_note"] is None
    assert row["market_value"] == 1100.0
    assert row["unrealized_pnl"] == 100.0
    assert row["unrealized_pnl_pct"] == pytest.approx(0.1)

    single = client.get(f"/api/v1/paper/accounts/{account.id}/positions/{asset.id}").json()
    assert single["mark_price"] == 110.0
    assert single["symbol"] == "TOTALS"


def test_a_position_without_a_closed_bar_has_no_mark_and_says_why(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="nomark")
    asset = _seed_asset(db_session, symbol="NOMARK")
    _seed_bars(db_session, asset, [100.0, 110.0], closed=False)
    account = _seed_account(db_session, cash=10_000.0)
    account.cash = Decimal("9000")
    db_session.add(
        PaperPosition(
            account_id=account.id,
            asset_id=asset.id,
            quantity=Decimal("10"),
            avg_cost=Decimal("100"),
            realized_pnl=Decimal("0"),
        )
    )
    db_session.commit()

    row = _positions(client, account.id)[0]

    assert row["mark_price"] is None
    assert row["mark_time"] is None
    assert row["market_value"] is None
    assert row["unrealized_pnl"] is None
    assert row["unrealized_pnl_pct"] is None
    assert "no closed bar" in row["mark_note"]

    body = _account_body(client, account.id)

    # An unvalued position makes the totals unknowable; the account says so rather than
    # marking it at cost, which would report a profit nobody has (ADR-007, ADR-023).
    assert body["market_value"] is None
    assert body["unrealized_pnl"] is None
    assert body["total_equity"] is None
    assert body["total_pnl"] is None
    assert body["total_pnl_pct"] is None
    assert body["cash"] == 9000.0
    assert body["net_deposits"] == 10_000.0
    assert body["realized_pnl"] == 0.0
    assert any("NOMARK" in note for note in body["metric_notes"])
    assert any("no closed bar" in note for note in body["metric_notes"])


def test_a_position_without_any_market_data_names_the_missing_series(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="nodata")
    asset = _seed_asset(db_session, symbol="NODATA")
    account = _seed_account(db_session, cash=1_000.0)
    db_session.add(
        PaperPosition(
            account_id=account.id,
            asset_id=asset.id,
            quantity=Decimal("1"),
            avg_cost=Decimal("100"),
            realized_pnl=Decimal("0"),
        )
    )
    db_session.commit()

    row = _positions(client, account.id)[0]

    assert row["mark_price"] is None
    assert row["mark_note"].startswith("no mark:")
    assert "no market data" in row["mark_note"]


def test_a_full_size_buy_is_not_reported_as_a_loss(client, db_session):
    """Cash alone reads as -100% after an all-in BUY; the position still has value."""

    _strategy, version = _seed_strategy(db_session, slug="noloss")
    asset = _seed_asset(db_session, symbol="NOLOSS")
    _seed_bars(db_session, asset, [100.0])
    account = _seed_account(db_session, cash=10_000.0, name="All in")
    signal = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    db_session.commit()

    _execute(client, account.id, signal_id=signal.id)
    body = _account_body(client, account.id)

    assert 0.0 <= body["cash"] < 1.0
    assert body["realized_pnl"] == 0.0
    assert body["market_value"] > 9_900.0
    assert body["total_equity"] > 9_900.0
    # Slippage and fees, not the whole deposit (ADR-124).
    assert -100.0 < body["total_pnl"] < 0.0


# --------------------------------------------------------------------------- #
# Explicit sizing: "three units" and "250 worth" are orders a person can place
# --------------------------------------------------------------------------- #


def test_execute_with_a_quantity_fills_exactly_that_quantity(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="qty")
    asset = _seed_asset(db_session, symbol="QTY")
    _seed_bars(db_session, asset, [100.0])
    account = _seed_account(db_session, cash=10_000.0)
    signal = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    db_session.commit()

    result = _execute(client, account.id, signal_id=signal.id, quantity="3")
    fill = 100.0 * 1.0005
    fees = 3 * fill * 0.001

    assert result["side"] == "BUY"
    assert result["quantity"] == 3.0
    assert result["fill_price"] == pytest.approx(fill)
    assert result["fees"] == pytest.approx(fees, rel=1e-9)
    assert result["cash"] == pytest.approx(10_000.0 - 3 * fill - fees, rel=1e-9)

    row = _positions(client, account.id)[0]
    assert row["quantity"] == 3.0
    assert row["avg_cost"] == pytest.approx(fill)
    # The mark is the stored close, so the position is worth the fill minus slippage.
    assert row["mark_price"] == 100.0


def test_execute_with_a_notional_buys_notional_over_price(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="notional")
    asset = _seed_asset(db_session, symbol="NOTIONAL")
    _seed_bars(db_session, asset, [100.0])
    account = _seed_account(db_session, cash=10_000.0)
    signal = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    db_session.commit()

    result = _execute(client, account.id, signal_id=signal.id, notional="250")
    fill = Decimal("100.05")
    expected = (Decimal("250") / fill).quantize(QUANTITY_SCALE, rounding=ROUND_DOWN)

    assert Decimal(str(result["quantity"])) == expected
    # The cash guard is on the amount, so the fill never exceeds what was asked for.
    assert Decimal(str(result["quantity"])) * Decimal(str(result["fill_price"])) <= Decimal("250")


def test_a_notional_larger_than_the_cash_is_refused(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="toobig")
    asset = _seed_asset(db_session, symbol="TOOBIG")
    _seed_bars(db_session, asset, [100.0])
    account = _seed_account(db_session, cash=1_000.0)
    signal = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/paper/accounts/{account.id}/execute",
        json={"signal_id": signal.id, "notional": "5000"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "notional exceeds available cash"


def test_a_quantity_and_a_notional_together_are_refused(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="both")
    asset = _seed_asset(db_session, symbol="BOTH")
    _seed_bars(db_session, asset, [100.0])
    account = _seed_account(db_session, cash=10_000.0)
    signal = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/paper/accounts/{account.id}/execute",
        json={"signal_id": signal.id, "quantity": "3", "notional": "250"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "specify either quantity or notional, not both"


def test_a_partial_sell_closes_only_the_requested_quantity(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="partial")
    asset = _seed_asset(db_session, symbol="PARTIAL")
    _seed_bars(db_session, asset, [100.0, 110.0])
    account = _seed_account(db_session, cash=10_000.0)
    buy = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    sell = _seed_signal(
        db_session,
        version=version,
        asset=asset,
        state="SELL",
        price=110.0,
        bar=BAR_START + dt.timedelta(days=1),
    )
    db_session.commit()

    bought = _execute(client, account.id, signal_id=buy.id, quantity="5")
    assert bought["quantity"] == 5.0

    sold = _execute(client, account.id, signal_id=sell.id, quantity="2")

    assert sold["side"] == "SELL"
    assert sold["quantity"] == 2.0
    assert sold["realized_pnl"] > 0.0

    row = _positions(client, account.id)[0]
    assert row["quantity"] == 3.0
    assert float(row["realized_pnl"]) == pytest.approx(sold["realized_pnl"])

    closed = db_session.scalars(
        select(PaperTrade).where(
            PaperTrade.account_id == account.id, PaperTrade.exit_time.is_not(None)
        )
    ).all()
    opened = db_session.scalars(
        select(PaperTrade).where(
            PaperTrade.account_id == account.id, PaperTrade.exit_time.is_(None)
        )
    ).all()
    # The slice that was sold is its own closed trade; the rest is still open. Without
    # this the account would report one trade for a position it only partly exited.
    assert [float(trade.quantity) for trade in closed] == [2.0]
    assert float(closed[0].pnl) == pytest.approx(sold["realized_pnl"])
    assert [float(trade.quantity) for trade in opened] == [3.0]


def test_a_closed_position_reports_no_ratio_and_no_negative_zero(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="flat")
    asset = _seed_asset(db_session, symbol="FLAT")
    _seed_bars(db_session, asset, [100.0, 90.0])
    account = _seed_account(db_session, cash=10_000.0)
    buy = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    sell = _seed_signal(
        db_session,
        version=version,
        asset=asset,
        state="SELL",
        price=90.0,
        bar=BAR_START + dt.timedelta(days=1),
    )
    db_session.commit()

    _execute(client, account.id, signal_id=buy.id, quantity="5")
    sold = _execute(client, account.id, signal_id=sell.id, quantity="5")
    assert sold["realized_pnl"] < 0.0

    row = _positions(client, account.id)[0]
    assert row["quantity"] == 0.0
    assert row["market_value"] == 0.0
    # Nothing is open any more, so there is no open return to publish: the mark sits below
    # the cost, and reporting that per-share move would show a loss on a flat position.
    # ``0 * negative`` is also ``-0.0`` in Decimal, which the UI prints as "-0.00".
    assert row["unrealized_pnl"] == 0.0
    assert str(row["unrealized_pnl"]) == "0.0"
    assert row["unrealized_pnl_pct"] is None

    totals = client.get(f"/api/v1/paper/accounts/{account.id}").json()
    assert totals["unrealized_pnl"] == 0.0
    assert totals["realized_pnl"] == pytest.approx(sold["realized_pnl"])
    assert totals["market_value"] == 0.0
    assert totals["total_equity"] == pytest.approx(totals["cash"])


def test_selling_more_than_the_position_holds_is_refused(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="oversell")
    asset = _seed_asset(db_session, symbol="OVERSELL")
    _seed_bars(db_session, asset, [100.0, 110.0])
    account = _seed_account(db_session, cash=10_000.0)
    buy = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    sell = _seed_signal(
        db_session,
        version=version,
        asset=asset,
        state="SELL",
        price=110.0,
        bar=BAR_START + dt.timedelta(days=1),
    )
    db_session.commit()

    _execute(client, account.id, signal_id=buy.id, quantity="5")
    response = client.post(
        f"/api/v1/paper/accounts/{account.id}/execute",
        json={"signal_id": sell.id, "quantity": "9"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "cannot sell more than the open position"


# --------------------------------------------------------------------------- #
# Reset: the history it promises to clear includes the orders
# --------------------------------------------------------------------------- #


def test_reset_leaves_no_orders_behind(client, db_session):
    _strategy, version = _seed_strategy(db_session, slug="reset")
    asset = _seed_asset(db_session, symbol="RESET")
    _seed_bars(db_session, asset, [100.0])
    account = _seed_account(db_session, cash=10_000.0)
    signal = _seed_signal(
        db_session, version=version, asset=asset, state="BUY", price=100.0, bar=BAR_START
    )
    db_session.commit()

    _execute(client, account.id, signal_id=signal.id, quantity="2")
    assert len(client.get(f"/api/v1/paper/orders?account_id={account.id}").json()) == 1

    response = client.post(f"/api/v1/paper/accounts/{account.id}/reset")

    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/paper/orders?account_id={account.id}").json() == []
    assert _positions(client, account.id) == []
    assert client.get(f"/api/v1/paper/accounts/{account.id}/trades").json() == []
    assert _account_body(client, account.id)["cash"] == 10_000.0


def test_reset_to_zero_keeps_the_requested_cash(client, db_session):
    account = _seed_account(db_session, cash=10_000.0)
    db_session.commit()

    response = client.post(f"/api/v1/paper/accounts/{account.id}/reset?initial_cash=0")

    assert response.status_code == 200, response.text
    assert response.json()["cash"] == 0.0
    assert response.json()["net_deposits"] == 0.0

    # No denominator, so the rate is absent and the account says why (ADR-066).
    body = _account_body(client, account.id)
    assert body["total_pnl_pct"] is None
    assert body["metric_notes"] == [
        "initial capital is not positive, so ratio metrics have no denominator"
    ]
