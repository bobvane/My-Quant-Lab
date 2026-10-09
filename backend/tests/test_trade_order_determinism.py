"""Order determinism for stored trade rows (ADR-197).

A plain ``SELECT`` promises no row order, and PostgreSQL is free to return heap order.
Three readers of ``BacktestTrade`` depend on the order they get:

* the run detail endpoint and its trades CSV (the user sees the trade sequence),
* the AI explanation, which reads the **first 20** trades,
* Monte Carlo, whose **seeded** RNG draws by *index* out of the P&L pool — so a different
  row order is a different distribution for the same request and the same seed.

SQLite cannot show this bug behaviourally: ``id INTEGER PRIMARY KEY`` *is* the rowid, so even
a full-table scan comes back in id order. That is precisely how the missing ``ORDER BY``
survives a green test suite here and only misbehaves on the NAS's PostgreSQL. So the tests
below assert the **SQL the application actually sends** (captured from ``before_cursor_execute``
while the request runs), plus the resulting order, and one of them measures what a different
pool order would do to the numbers.
"""

from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import event, select

from app.api.schemas import MarketDataSyncRequest
from app.domain.models import BacktestRun, BacktestTrade
from app.research.monte_carlo import run_monte_carlo

_SYMBOL = "DEMO-AAPL"
_CAPITAL = 10_000.0

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "trade-order", "name": "Trade order", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": _CAPITAL},
}

_PNL_BY_ID_ORDER = (120.0, -80.0, 30.0)
_A_DIFFERENT_ORDER = (30.0, 120.0, -80.0)


def _completed_run(client) -> tuple[int, int]:
    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    strategy = client.post("/api/v1/strategies", json={"name": "Trade order"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(_DSL)},
    ).json()
    run = client.post(
        "/api/v1/backtests",
        json={"strategy_version_id": version["id"], "symbol": _SYMBOL, "timeframe": "1d"},
    ).json()
    return int(run["id"]), int(version["id"])


def _three_trades(db_session, run_id: int) -> None:
    """Replace the engine's trades with three known P&L values, in id order."""

    for row in db_session.scalars(
        select(BacktestTrade).where(BacktestTrade.backtest_run_id == run_id)
    ).all():
        db_session.delete(row)
    db_session.commit()

    entry = dt.datetime(2025, 1, 2, tzinfo=dt.UTC)
    for offset, pnl in enumerate(_PNL_BY_ID_ORDER):
        db_session.add(
            BacktestTrade(
                backtest_run_id=run_id,
                symbol=_SYMBOL,
                direction="LONG",
                entry_time=entry + dt.timedelta(days=offset),
                entry_price=Decimal("100"),
                exit_time=entry + dt.timedelta(days=offset + 1),
                exit_price=Decimal("101"),
                quantity=Decimal("1"),
                fees=Decimal("0"),
                slippage=Decimal("0"),
                pnl=Decimal(str(pnl)),
                strategy_version="1.0.0",
            )
        )
    db_session.commit()
    db_session.expire_all()


def _trade_selects_during(db_session, call: Callable[[], Any]) -> tuple[Any, list[str]]:
    """Run ``call`` and return its result plus every SELECT that read ``backtest_trades``."""

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        flat = " ".join(str(statement).split())
        if "backtest_trades" in flat and flat.upper().startswith("SELECT"):
            statements.append(flat)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        result = call()
    finally:
        event.remove(bind, "before_cursor_execute", record)
    return result, statements


def test_the_trades_relationship_declares_its_order() -> None:
    """The relationship itself pins the order, so every caller inherits it (ADR-197)."""

    assert BacktestRun.trades.property.order_by, (
        "BacktestRun.trades must declare order_by=BacktestTrade.id"
    )


def test_the_detail_endpoint_orders_the_trades_it_sends(client, db_session) -> None:
    run_id, _ = _completed_run(client)
    _three_trades(db_session, run_id)

    body, selects = _trade_selects_during(
        db_session, lambda: client.get(f"/api/v1/backtests/{run_id}").json()
    )

    assert selects, "the detail endpoint must read backtest_trades"
    for statement in selects:
        assert "ORDER BY backtest_trades.id" in statement, statement
    assert [t["pnl"] for t in body["trades"]] == list(_PNL_BY_ID_ORDER)


def test_the_resample_pool_is_ordered_by_id(client, db_session) -> None:
    run_id, _ = _completed_run(client)
    _three_trades(db_session, run_id)

    body, selects = _trade_selects_during(
        db_session,
        lambda: client.post(
            "/api/v1/research/monte-carlo",
            json={"backtest_run_id": run_id, "runs": 100, "seed": 5},
        ).json(),
    )

    assert selects, "the resample must read backtest_trades"
    for statement in selects:
        assert "ORDER BY backtest_trades.id" in statement, statement

    # The measured consequence: the RNG draws indices into the pool, so a permuted pool puts
    # a different P&L at each draw index and the same seed yields different equity paths.
    ordered = run_monte_carlo(
        [{"pnl": pnl} for pnl in _PNL_BY_ID_ORDER],
        initial_capital=_CAPITAL,
        runs=100,
        seed=5,
        timeframe="1d",
    )
    other = run_monte_carlo(
        [{"pnl": pnl} for pnl in _A_DIFFERENT_ORDER],
        initial_capital=_CAPITAL,
        runs=100,
        seed=5,
        timeframe="1d",
    )
    assert body["sample_equity_paths"] == ordered["sample_equity_paths"]
    assert body["sample_equity_paths"] != other["sample_equity_paths"]
    assert ordered["sample_equity_paths"] != other["sample_equity_paths"]


def test_the_monte_carlo_experiment_reads_the_same_ordered_pool(client, db_session) -> None:
    run_id, version_id = _completed_run(client)
    _three_trades(db_session, run_id)

    def create_and_read() -> dict:
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Ordered pool",
                "kind": "monte_carlo",
                "strategy_version_id": version_id,
                "backtest_run_id": run_id,
                "runs": 100,
                "seed": 5,
            },
        )
        assert created.status_code == 201, created.text
        return client.get(f"/api/v1/experiments/{created.json()['id']}").json()

    body, selects = _trade_selects_during(db_session, create_and_read)

    assert selects, "the experiment must read backtest_trades"
    for statement in selects:
        assert "ORDER BY backtest_trades.id" in statement, statement

    results = body["results"]
    assert len(results) == 1
    ordered = run_monte_carlo(
        [{"pnl": pnl} for pnl in _PNL_BY_ID_ORDER],
        initial_capital=_CAPITAL,
        runs=100,
        seed=5,
        timeframe="1d",
    )
    assert results[0]["payload"]["sample_equity_paths"] == ordered["sample_equity_paths"]


def test_a_permuted_pool_would_change_the_number_the_user_reads() -> None:
    """Why the ORDER BY is load-bearing: it is not cosmetic.

    The RNG draws *indices* into the pool, so permuting the pool changes which P&L lands at
    each draw index: the same seed over the same three trades then produces different equity
    paths — and therefore a different drawdown distribution, which is the "worst case" figure
    the user is shown.
    """

    ordered = run_monte_carlo(
        [{"pnl": pnl} for pnl in _PNL_BY_ID_ORDER],
        initial_capital=_CAPITAL,
        runs=500,
        seed=11,
        timeframe="1d",
    )
    other = run_monte_carlo(
        [{"pnl": pnl} for pnl in _A_DIFFERENT_ORDER],
        initial_capital=_CAPITAL,
        runs=500,
        seed=11,
        timeframe="1d",
    )

    assert ordered["sample_equity_paths"] != other["sample_equity_paths"]
    assert ordered["summary"]["total_return"] != other["summary"]["total_return"]
    # The drawdown percentiles are *not* asserted: with a three-trade pool the same handful
    # of multisets recur often enough that the percentiles can coincide. The paths above are
    # the load-bearing evidence that the pool's order reaches the numbers.
