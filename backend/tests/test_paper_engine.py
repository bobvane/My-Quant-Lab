"""Paper-trading execution tests (docs/08, docs/15 Phase 4).

Paper accounts are virtual and isolated; these tests pin the accounting: a BUY
spends cash and opens a position, a SELL realises P&L (net of both fees), and an
empty or closed account refuses to trade.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain.models import (
    Asset,
    PaperAccount,
    PaperPosition,
    PaperTrade,
    Signal,
    Strategy,
    StrategyVersion,
)
from app.simulation.paper_engine import PaperError, PaperExecutionSettings, execute_signal


def _seed(db, *, cash: float = 10_000, state: str = "BUY", price: float = 100.0):
    strategy = Strategy(name="Paper", slug="paper-strat")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="p" * 64
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol="PAPER", asset_class="stock")
    db.add(asset)
    db.flush()
    account = PaperAccount(name="PA", initial_cash=cash, cash=cash)
    db.add(account)
    db.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state=state,
        direction="LONG",
        price_reference=price,
        triggered_rules_json=["rule"],
        feature_snapshot_hash="f" * 64,
        data_source="test",
    )
    db.add(signal)
    db.commit()
    return account, signal, asset


def test_buy_spends_cash_and_opens_position(db_session) -> None:
    account, signal, asset = _seed(db_session)
    result = execute_signal(
        db_session, account, signal, settings=PaperExecutionSettings(fee_bps=10, slippage_bps=5)
    )
    assert result["side"] == "BUY"
    # All-in: cash is (near) zero, and slippage makes the fill price above 100.
    assert float(account.cash) == pytest.approx(0.0, abs=1e-6)
    assert float(result["fill_price"]) == pytest.approx(100.05, abs=1e-6)
    assert float(result["fees"]) > 0

    position = db_session.query(PaperPosition).one()
    assert float(position.quantity) > 0
    assert float(position.avg_cost) == pytest.approx(100.05, abs=1e-6)

    trade = db_session.query(PaperTrade).one()
    assert trade.exit_time is None  # still open
    assert trade.strategy_version is not None and trade.strategy_version.endswith("@1.0.0")


def test_sell_closes_position_and_realises_pnl(db_session) -> None:
    account, buy_signal, asset = _seed(db_session)
    execute_signal(db_session, account, buy_signal, settings=PaperExecutionSettings())

    sell = Signal(
        strategy_version_id=buy_signal.strategy_version_id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
        state="SELL",
        direction="FLAT",
        price_reference=110.0,
        feature_snapshot_hash="g" * 64,
        data_source="test",
    )
    db_session.add(sell)
    db_session.commit()

    result = execute_signal(db_session, account, sell, settings=PaperExecutionSettings())
    assert result["side"] == "SELL"
    assert float(result["realized_pnl"]) > 0
    # Cash is back above zero (sold the full position at a profit).
    assert float(account.cash) > 10_000
    position = db_session.query(PaperPosition).one()
    assert float(position.quantity) == 0

    trade = db_session.query(PaperTrade).one()
    assert trade.exit_time is not None
    assert float(trade.pnl) == pytest.approx(float(result["realized_pnl"]), abs=1e-6)


def test_sell_without_position_is_refused(db_session) -> None:
    account, _signal, asset = _seed(db_session)
    sell = Signal(
        strategy_version_id=_signal.strategy_version_id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
        state="SELL",
        direction="FLAT",
        price_reference=110.0,
        feature_snapshot_hash="g" * 64,
        data_source="test",
    )
    db_session.add(sell)
    db_session.commit()
    with pytest.raises(PaperError):
        execute_signal(db_session, account, sell)


def test_double_buy_is_refused(db_session) -> None:
    account, signal, asset = _seed(db_session)
    execute_signal(db_session, account, signal)
    second = Signal(
        strategy_version_id=signal.strategy_version_id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 4, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=101.0,
        feature_snapshot_hash="h" * 64,
        data_source="test",
    )
    db_session.add(second)
    db_session.commit()
    with pytest.raises(PaperError):
        execute_signal(db_session, account, second)


def test_rebuy_after_sell_reuses_position_row(db_session) -> None:
    """Regression: buy → sell → buy used to insert a second position row for
    the same (account, asset), violating the unique constraint (HTTP 500)."""

    account, buy_signal, asset = _seed(db_session)
    execute_signal(db_session, account, buy_signal)
    first_row = db_session.query(PaperPosition).one()
    first_id = first_row.id

    sell = Signal(
        strategy_version_id=buy_signal.strategy_version_id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
        state="SELL",
        direction="FLAT",
        price_reference=110.0,
        feature_snapshot_hash="g" * 64,
        data_source="test",
    )
    db_session.add(sell)
    db_session.commit()
    execute_signal(db_session, account, sell)
    assert float(db_session.query(PaperPosition).one().quantity) == 0

    rebuy = Signal(
        strategy_version_id=buy_signal.strategy_version_id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 5, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=105.0,
        feature_snapshot_hash="r" * 64,
        data_source="test",
    )
    db_session.add(rebuy)
    db_session.commit()

    result = execute_signal(db_session, account, rebuy)
    assert result["side"] == "BUY"
    assert db_session.query(PaperPosition).count() == 1  # reused, not duplicated
    reused = db_session.query(PaperPosition).one()
    assert reused.id == first_id
    assert float(reused.quantity) > 0
    assert float(reused.avg_cost) > 0


def test_closed_account_is_refused(db_session) -> None:
    account, signal, _asset = _seed(db_session)
    account.status = "closed"
    db_session.commit()
    with pytest.raises(PaperError):
        execute_signal(db_session, account, signal)


def test_audit_records_execution(db_session) -> None:
    from app.domain.models import AuditLog

    account, signal, _asset = _seed(db_session)
    execute_signal(db_session, account, signal)
    events = [
        row for row in db_session.query(AuditLog).all() if row.event_type == "paper_order_executed"
    ]
    assert len(events) == 1
    assert events[0].payload_json["side"] == "BUY"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_api_execute_positions_and_status(client, db_session) -> None:
    account = client.post(
        "/api/v1/paper/accounts", json={"name": "API PA", "initial_cash": 5000}
    ).json()
    asset = client.post(
        "/api/v1/assets", json={"symbol": "APIPAPER", "asset_class": "stock"}
    ).json()

    # The signal table has no public create endpoint, so seed the row directly
    # through the same session the client is wired to.
    strategy = Strategy(name="API Paper", slug="api-paper")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="q" * 64
    )
    db_session.add(version)
    db_session.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset["id"],
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=50.0,
        feature_snapshot_hash="z" * 64,
        data_source="test",
    )
    db_session.add(signal)
    db_session.commit()
    signal_id = signal.id

    executed = client.post(
        f"/api/v1/paper/accounts/{account['id']}/execute", json={"signal_id": signal_id}
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["side"] == "BUY"

    positions = client.get(f"/api/v1/paper/accounts/{account['id']}/positions").json()
    assert len(positions) == 1

    closed = client.post(f"/api/v1/paper/accounts/{account['id']}/close")
    assert closed.json()["status"] == "closed"

    blocked = client.post(
        f"/api/v1/paper/accounts/{account['id']}/execute", json={"signal_id": signal_id}
    )
    assert blocked.status_code == 422

    reopened = client.post(f"/api/v1/paper/accounts/{account['id']}/reopen")
    assert reopened.json()["status"] == "active"


def test_api_paper_performance(client, db_session) -> None:
    from app.domain.models import Asset, PaperTrade

    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Perf PA", "initial_cash": 1000}
    ).json()
    asset = Asset(symbol="PERF", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        PaperTrade(
            account_id=account["id"],
            asset_id=asset.id,
            direction="LONG",
            entry_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            entry_price=10.0,
            exit_time=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
            exit_price=11.0,
            quantity=10.0,
            pnl=10.0,
        )
    )
    db_session.commit()

    body = client.get(f"/api/v1/paper/accounts/{account['id']}/performance").json()
    assert body["closed_trades"] == 1
    assert body["final_equity"] == 1010.0
    assert body["metrics"]["number_of_trades"] == 1
    assert client.get("/api/v1/paper/accounts/9999/performance").status_code == 404


def test_api_fund_and_withdraw_limits(client) -> None:
    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Fund PA", "initial_cash": 1000}
    ).json()
    # Responses name the money the account holds, not the number typed at creation:
    # funding moves the baseline, so `initial_cash` would be a lie (ADR-066).
    assert account["net_deposits"] == pytest.approx(1000)
    assert "initial_cash" not in account

    added = client.post(f"/api/v1/paper/accounts/{account['id']}/fund", json={"amount": 500})
    assert added.status_code == 200
    assert added.json()["cash"] == pytest.approx(1500)
    assert added.json()["net_deposits"] == pytest.approx(1500)

    too_much = client.post(f"/api/v1/paper/accounts/{account['id']}/fund", json={"amount": -9999})
    assert too_much.status_code == 422


def _book_a_closed_trade(db, *, account_id: int, pnl: float, cash: float) -> None:
    """Record a closed trade and the cash it left behind, as the engine would."""
    asset = Asset(symbol=f"PA-{account_id}-{int(pnl)}", asset_class="stock")
    db.add(asset)
    db.flush()
    db.add(
        PaperTrade(
            account_id=account_id,
            asset_id=asset.id,
            direction="LONG",
            entry_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            entry_price=10.0,
            exit_time=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
            exit_price=11.0,
            quantity=1.0,
            pnl=pnl,
        )
    )
    account = db.get(PaperAccount, account_id)
    account.cash = cash
    db.commit()


def test_a_withdrawal_is_not_a_trading_loss(client) -> None:
    """Taking money out lowers the baseline with it (ADR-066).

    A withdrawal used to lower only `cash`, so an account nobody had traded was
    published as -40% and `final_equity` kept describing money that had already left.
    """
    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Withdraw PA", "initial_cash": 10_000}
    ).json()

    moved = client.post(f"/api/v1/paper/accounts/{account['id']}/fund", json={"amount": -4_000})
    assert moved.status_code == 200
    assert moved.json()["cash"] == pytest.approx(6_000)
    assert moved.json()["net_deposits"] == pytest.approx(6_000)

    equity = client.get(f"/api/v1/paper/accounts/{account['id']}/equity").json()
    assert equity["cash"] == pytest.approx(6_000)
    assert equity["net_deposits"] == pytest.approx(6_000)
    assert equity["realized_pnl"] == pytest.approx(0)
    # Nothing was traded: cash equals the money the account holds, so the P&L is zero.
    assert equity["cash"] - equity["net_deposits"] == pytest.approx(0)

    row = next(
        item for item in client.get("/api/v1/paper/accounts").json() if item["id"] == account["id"]
    )
    assert row["net_deposits"] == pytest.approx(6_000)
    assert "initial_cash" not in row


def test_a_profitable_account_still_reports_a_profit_after_a_withdrawal(client, db_session) -> None:
    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Winner PA", "initial_cash": 10_000}
    ).json()
    _book_a_closed_trade(db_session, account_id=account["id"], pnl=1_000.0, cash=11_000.0)

    moved = client.post(f"/api/v1/paper/accounts/{account['id']}/fund", json={"amount": -4_000})
    assert moved.json()["cash"] == pytest.approx(7_000)
    assert moved.json()["net_deposits"] == pytest.approx(6_000)

    body = client.get(f"/api/v1/paper/accounts/{account['id']}/performance").json()
    assert body["net_deposits"] == pytest.approx(6_000)
    assert body["final_equity"] == pytest.approx(7_000)
    assert body["final_equity"] == pytest.approx(moved.json()["cash"])
    # +1,000 earned on the 10,000 the trade actually ran on. The withdrawn 4,000 did not
    # make the return bigger: funding moves the baseline, not the result (ADR-121).
    assert body["metrics"]["total_return"] == pytest.approx(0.10, rel=1e-9)
    assert body["metrics"]["total_return"] > 0


def test_withdrawing_past_the_deposits_publishes_no_return(client, db_session) -> None:
    """When net deposits are not positive there is no denominator, so no percentage."""
    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Past PA", "initial_cash": 10_000}
    ).json()
    _book_a_closed_trade(db_session, account_id=account["id"], pnl=1_000.0, cash=11_000.0)

    moved = client.post(f"/api/v1/paper/accounts/{account['id']}/fund", json={"amount": -11_000})
    assert moved.json()["cash"] == pytest.approx(0)
    assert moved.json()["net_deposits"] == pytest.approx(-1_000)

    body = client.get(f"/api/v1/paper/accounts/{account['id']}/performance").json()
    assert body["final_equity"] == pytest.approx(0)
    assert body["metrics"]["total_return"] is None
    assert body["metrics"]["max_drawdown"] is None
    assert "no denominator" in " ".join(body["metric_notes"])


def test_an_emptied_account_names_the_denominator_it_lost(client, db_session) -> None:
    """An account withdrawn to zero has a one-point curve; the missing denominator is
    still the reason the ratios are withheld, and it must be the reason that is said
    out loud (found by the v1.5.5 browser check, where the card blamed a young account)."""
    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Emptied PA", "initial_cash": 10_000}
    ).json()

    moved = client.post(f"/api/v1/paper/accounts/{account['id']}/fund", json={"amount": -10_000})
    assert moved.json()["net_deposits"] == pytest.approx(0)

    body = client.get(f"/api/v1/paper/accounts/{account['id']}/performance").json()
    assert body["final_equity"] == pytest.approx(0)
    assert body["metrics"]["total_return"] is None
    assert body["metric_notes"] == [
        "initial capital is not positive, so ratio metrics have no denominator"
    ]
