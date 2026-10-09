"""Paper trading endpoints.

Paper accounts are **completely isolated** from the real portfolio: they live in
their own tables, are funded with virtual cash and can never write to Ghostfolio.

The account view marks open positions at the latest **closed** bar, read through the
shared market-data loader, so "market value" and "total equity" describe the virtual
account from paper rows and public bars alone (ADR-006, ADR-119).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    PaperAccountCreate,
    PaperAccountOut,
    PaperExecuteRequest,
    PaperExecutionOut,
    PaperFundRequest,
    PaperOrderOut,
    PaperPositionOut,
)
from app.core.db import get_db
from app.data.market_data_repo import SeriesNotResolved, load_bars, resolve_series
from app.data.strategy_service import record_audit
from app.domain.models import (
    AuditLog,
    BacktestRun,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    Signal,
    Strategy,
    StrategyVersion,
)
from app.simulation.paper_engine import (
    PaperError,
    PaperExecutionSettings,
    execute_signal,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/paper", tags=["paper-trading"])


def _realized_by_account(db: Session, account_ids: list[int]) -> dict[int, float]:
    """Realized P&L per account, in one query.

    The account list is what the paper panel and the dashboard read, and neither may
    present `cash - net_deposits` as the P&L: a position that is open spent the cash, so
    a full-size buy used to be shown as -100% (ADR-124).
    """
    if not account_ids:
        return {}
    rows = db.execute(
        select(PaperTrade.account_id, func.sum(PaperTrade.pnl))
        .where(PaperTrade.account_id.in_(account_ids), PaperTrade.exit_time.is_not(None))
        .group_by(PaperTrade.account_id)
    ).all()
    return {int(account_id): float(total or 0.0) for account_id, total in rows}


def _account_positions(db: Session, account_id: int) -> list[PaperPosition]:
    """Every position row of an account, including fully closed ones (quantity 0)."""

    return list(
        db.scalars(
            select(PaperPosition)
            .where(PaperPosition.account_id == account_id)
            .order_by(PaperPosition.id)
        ).all()
    )


def _position_mark(
    db: Session, asset_id: int
) -> tuple[Decimal | None, dt.datetime | None, str | None]:
    """Close of the latest *closed* bar for ``asset_id``, or why there is no mark.

    The mark has to come from the one market-data loader rather than a hand-written bar
    query, so "the latest closed bar" means the same thing here as it does everywhere
    else (ADR-119). When nothing qualifies the caller gets ``None`` plus a note instead of
    a price: a position without a mark is a gap to name, while a cost-based, intraday or
    invented price would be a fabricated fact (ADR-007, ADR-023).
    """

    try:
        series = resolve_series(db, asset_id=asset_id)
    except SeriesNotResolved as exc:
        return None, None, f"no mark: {exc.detail}"
    frame = load_bars(db, series, limit=1, only_closed=True)
    if frame.empty:
        return None, None, "no mark: no closed bar is stored for this asset yet"
    close = frame["close"].iloc[-1]
    if not math.isfinite(float(close)):
        return None, None, "no mark: the latest closed bar carries no close price"
    stamp = frame.index[-1]
    moment = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
    return Decimal(str(close)), _aware(moment), None


def _position_payload(db: Session, position: PaperPosition) -> PaperPositionOut:
    """One position row, marked at the latest closed bar of its asset.

    The mark fields stay ``null`` when no bar qualifies so the UI can show "no price
    yet" rather than a number nobody traded at (ADR-007).
    """

    symbol = position.asset.symbol if position.asset is not None else None
    mark_price, mark_time, mark_note = _position_mark(db, position.asset_id)
    payload = PaperPositionOut.model_validate(position).model_copy(update={"symbol": symbol})
    if mark_price is None:
        return payload.model_copy(update={"mark_note": mark_note})
    quantity = Decimal(str(position.quantity))
    avg_cost = Decimal(str(position.avg_cost))
    unrealized = quantity * (mark_price - avg_cost)
    if not unrealized:
        # A zero quantity multiplies a negative spread into ``Decimal("-0.0")``, which the
        # UI then prints as "-0.00": a flat position has no loss to show.
        unrealized = Decimal(0)
    # A ratio, not a percentage: `0.05` means +5% (ADR-087). The ratio measures the open
    # exposure, so it needs a non-zero quantity as well as a non-zero cost: a closed
    # position has no return left to publish, and the per-share move would report a loss
    # on a position that is flat.
    pct = unrealized / (avg_cost * quantity) if quantity > 0 and avg_cost > 0 else None
    return payload.model_copy(
        update={
            "mark_price": float(mark_price),
            "mark_time": mark_time,
            "market_value": float(quantity * mark_price),
            "unrealized_pnl": float(unrealized),
            "unrealized_pnl_pct": float(pct) if pct is not None else None,
        }
    )


def _strategy_name(db: Session, account: PaperAccount) -> str | None:
    """The strategy's display name, from the bound version when there is one.

    The version is what actually produced the result; the legacy ``strategy_id`` column
    stays as the fallback for accounts created before the binding existed (ADR-181).
    """

    if account.strategy_version_id is not None:
        version = db.get(StrategyVersion, account.strategy_version_id)
        if version is not None:
            strategy = db.get(Strategy, version.strategy_id)
            if strategy is not None:
                return strategy.name
    if account.strategy_id is not None:
        strategy = db.get(Strategy, account.strategy_id)
        if strategy is not None:
            return strategy.name
    return None


def _account_totals(db: Session, account: PaperAccount, *, realized: float) -> dict:
    """Cash plus the marked value of what is open, from paper data only.

    Cash and realised P&L alone hide an entire open position: a full-size buy spends the
    cash, so the account reads as though the money were gone instead of held (ADR-181).
    Every input is a paper row plus the shared market-data loader, so nothing here can
    reach the real portfolio (ADR-006).

    When an open position has no mark, the totals could only describe part of the
    account, so they are published as ``null`` with the reason in ``metric_notes``: a
    total that silently drops a position is a fabricated total (ADR-007, ADR-023). P&L is
    never derived by differencing cash (ADR-124), which would report an open position as
    a loss.
    """

    positions = [
        row for row in _account_positions(db, account.id) if Decimal(str(row.quantity)) > 0
    ]
    market_value = Decimal(0)
    unrealized = Decimal(0)
    notes: list[str] = []
    unmarked = False
    for position in positions:
        mark_price, _mark_time, mark_note = _position_mark(db, position.asset_id)
        if mark_price is None:
            unmarked = True
            label = position.asset.symbol if position.asset is not None else str(position.asset_id)
            notes.append(f"position {label} is not in the totals: {mark_note}")
            continue
        quantity = Decimal(str(position.quantity))
        market_value += quantity * mark_price
        unrealized += quantity * (mark_price - Decimal(str(position.avg_cost)))

    cash = Decimal(str(account.cash))
    net_deposits = Decimal(str(account.initial_cash))
    if net_deposits <= 0:
        # ADR-066: return-type metrics divide by net deposits, so an account whose
        # principal was withdrawn has no denominator — name that instead of dividing.
        notes.append("initial capital is not positive, so ratio metrics have no denominator")
    if unmarked:
        return {
            "market_value": None,
            "unrealized_pnl": None,
            "total_equity": None,
            "total_pnl": None,
            "total_pnl_pct": None,
            "metric_notes": notes,
        }
    total_pnl = Decimal(str(realized)) + unrealized
    return {
        "market_value": float(market_value),
        "unrealized_pnl": float(unrealized),
        "total_equity": float(cash + market_value),
        "total_pnl": float(total_pnl),
        "total_pnl_pct": float(total_pnl / net_deposits) if net_deposits > 0 else None,
        "metric_notes": notes,
    }


def _account_payload(
    db: Session, account: PaperAccount, *, realized: float | None = None
) -> PaperAccountOut:
    if realized is None:
        realized = _realized_by_account(db, [account.id]).get(account.id, 0.0)
    payload = PaperAccountOut.model_validate(account).model_copy(
        update={"realized_pnl": realized, "strategy_name": _strategy_name(db, account)}
    )
    return payload.model_copy(update=_account_totals(db, account, realized=realized))


@router.get("/accounts", response_model=list[PaperAccountOut], summary="List paper accounts")
def list_accounts(db: Session = Depends(get_db)) -> list[PaperAccountOut]:
    rows = db.scalars(select(PaperAccount).order_by(PaperAccount.id)).all()
    realized = _realized_by_account(db, [row.id for row in rows])
    return [_account_payload(db, row, realized=realized.get(row.id, 0.0)) for row in rows]


@router.post(
    "/accounts", response_model=PaperAccountOut, status_code=201, summary="Create paper account"
)
def create_account(payload: PaperAccountCreate, db: Session = Depends(get_db)) -> PaperAccountOut:
    """Open a paper account, optionally bound to the backtest that produced it.

    The binding records the strategy **version** and the exact parameters the run used,
    because a strategy id alone cannot answer "which strategy, with which settings" once
    the strategy has moved on — and a version is immutable, so the answer stays true
    (ADR-181). Only a completed run has results to bind to.
    """

    backtest_run_id = payload.backtest_run_id
    strategy_version_id = payload.strategy_version_id
    parameters = dict(payload.parameters) if payload.parameters is not None else None

    if backtest_run_id is not None:
        run = db.get(BacktestRun, backtest_run_id)
        if run is None:
            raise HTTPException(status_code=422, detail=f"backtest run {backtest_run_id} not found")
        if run.status != "completed":
            # A run that is pending/running/failed has no result to open an account
            # from; binding to it would record numbers that do not exist yet.
            raise HTTPException(
                status_code=422,
                detail=f"backtest run {backtest_run_id} is '{run.status}', not 'completed'",
            )
        if strategy_version_id is None:
            strategy_version_id = run.strategy_version_id
        if parameters is None:
            parameters = dict(run.parameters_json or {})

    strategy_id = payload.strategy_id
    if strategy_version_id is not None:
        version = db.get(StrategyVersion, strategy_version_id)
        if version is None:
            raise HTTPException(
                status_code=422, detail=f"strategy version {strategy_version_id} not found"
            )
        if strategy_id is not None and strategy_id != version.strategy_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"strategy {strategy_id} does not own strategy version "
                    f"{strategy_version_id}; the version decides the strategy"
                ),
            )
        # Keep the legacy column meaning what it always meant: the strategy this
        # account trades. The version is the authority, so it wins over the caller.
        strategy_id = version.strategy_id

    account = PaperAccount(
        name=payload.name,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        backtest_run_id=backtest_run_id,
        parameters_json=parameters or {},
        base_currency=payload.base_currency,
        initial_cash=payload.initial_cash,
        cash=payload.initial_cash,
        settings_json=payload.settings,
    )
    db.add(account)
    db.flush()
    record_audit(
        db,
        event_type="paper_account_created",
        entity_type="paper_account",
        entity_id=str(account.id),
        action="create",
        payload={
            "name": account.name,
            "net_deposits": str(account.initial_cash),
            "strategy_version_id": account.strategy_version_id,
            "backtest_run_id": account.backtest_run_id,
        },
    )
    db.commit()
    db.refresh(account)
    return _account_payload(db, account, realized=0.0)


@router.get("/accounts/{account_id}", response_model=PaperAccountOut, summary="Get paper account")
def get_account(account_id: int, db: Session = Depends(get_db)) -> PaperAccountOut:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    return _account_payload(db, account)


def _aware(moment: dt.datetime) -> dt.datetime:
    """SQLite hands back naive timestamps while the audit log writes UTC-aware ones."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)


def _trade_span(trades: list[PaperTrade]) -> dt.timedelta | None:
    """How much time the trades actually cover, or None when they cover none.

    A metric series with one point per trade is not a calendar: annualising it as if
    each point were a day turned two trades a few weeks apart into a four-digit CAGR.
    """
    entries = [_aware(t.entry_time) for t in trades if t.entry_time is not None]
    exits = [_aware(t.exit_time) for t in trades if t.exit_time is not None]
    if not entries or not exits:
        return None
    span = max(exits) - min(entries)
    return span if span > dt.timedelta(0) else None


def _account_life(
    db: Session, account: PaperAccount
) -> tuple[dt.datetime, float, list[tuple[dt.datetime, float]], list[PaperTrade]]:
    """What moved this account, and when: start, opening cash, funding, closed trades.

    The equity curve and the performance metrics must agree about the account's
    current life, so both read it from here instead of each rebuilding it.
    """
    closed = list(
        db.scalars(
            select(PaperTrade)
            .where(PaperTrade.account_id == account.id, PaperTrade.exit_time.is_not(None))
            .order_by(PaperTrade.exit_time)
        ).all()
    )
    events = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "paper_account",
            AuditLog.entity_id == str(account.id),
            AuditLog.event_type.in_(("paper_account_funded", "paper_account_reset")),
        )
        .order_by(AuditLog.created_at)
    ).all()
    resets = [event for event in events if event.event_type == "paper_account_reset"]
    # A reset deletes the trades and installs a new opening cash, so the curve covers
    # the current life of the account and nothing before it.
    start = _aware(resets[-1].created_at) if resets else _aware(account.created_at)
    flows = [
        (_aware(event.created_at), float((event.payload_json or {}).get("amount") or 0.0))
        for event in events
        if event.event_type == "paper_account_funded" and _aware(event.created_at) >= start
    ]
    # Whatever was not put in after the opening is what the account started with.
    opening = float(account.initial_cash) - sum(amount for _, amount in flows)
    return start, opening, flows, closed


def replay_equity_curve(db: Session, account: PaperAccount) -> list[dict[str, float | str]]:
    """Rebuild an account's equity history from the events that moved it (ADR-108).

    Funding is read back from the audit log instead of assumed, because a deposit
    moves the baseline: a curve drawn as "today's net deposits plus realized P&L"
    would rewrite the past every time money entered or left the account.
    """
    start, opening, flows, closed = _account_life(db, account)
    moves: list[tuple[dt.datetime, float]] = list(flows)
    moves += [
        (_aware(trade.exit_time), float(trade.pnl or 0))
        for trade in closed
        if trade.exit_time is not None and _aware(trade.exit_time) >= start
    ]
    moves.sort(key=lambda move: move[0])
    points: list[dict[str, float | str]] = [
        {"timestamp": start.isoformat(), "equity": round(opening, 4)}
    ]
    equity = opening
    for moment, delta in moves:
        equity += delta
        points.append({"timestamp": moment.isoformat(), "equity": round(equity, 4)})
    return points


@router.get("/accounts/{account_id}/equity", summary="Equity curve of a paper account")
def account_equity(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    trades = db.scalars(
        select(PaperTrade)
        .where(PaperTrade.account_id == account_id)
        .order_by(PaperTrade.entry_time)
    ).all()
    realized = sum(float(t.pnl or 0) for t in trades if t.exit_time is not None)
    positions = db.scalars(
        select(PaperPosition).where(PaperPosition.account_id == account_id)
    ).all()
    curve = replay_equity_curve(db, account)
    return {
        "account_id": account_id,
        "cash": float(account.cash),
        "net_deposits": float(account.initial_cash),
        "realized_pnl": realized,
        "positions": [
            {
                "asset_id": p.asset_id,
                "quantity": float(p.quantity),
                "avg_cost": float(p.avg_cost),
                "realized_pnl": float(p.realized_pnl),
            }
            for p in positions
        ],
        "trades_count": len(trades),
        "equity_curve": curve,
        "curve_note": (
            "权益曲线的每个点都是那个时刻的账户权益：入金/提现抬高或压低基准"
            "（钱进来不是盈利，见 ADR-066），已平仓交易加上它的已实现盈亏；"
            "最后一个点等于 净入金 + 已实现盈亏。重置会删掉全部交易并把基准"
            "重新设成新的期初现金，所以曲线只覆盖账户当前这段生命周期（ADR-108）。"
        ),
        "note": (
            "Paper accounts are virtual and fully isolated from real holdings. "
            "net_deposits is the money the account was funded with (deposits minus "
            "withdrawals) and is the baseline for its P&L (ADR-066)."
        ),
    }


@router.get(
    "/accounts/{account_id}/performance",
    summary="Performance metrics for a paper account (docs/08 §5)",
)
def account_performance(account_id: int, db: Session = Depends(get_db)) -> dict:
    import numpy as np

    from app.research.metrics import compute_metrics

    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")

    trades = list(
        db.scalars(
            select(PaperTrade)
            .where(PaperTrade.account_id == account_id, PaperTrade.exit_time.is_not(None))
            .order_by(PaperTrade.exit_time)
        ).all()
    )
    net_deposits = float(account.initial_cash)
    trade_dicts = [
        {
            "pnl": float(t.pnl or 0),
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "direction": t.direction,
        }
        for t in trades
    ]
    if net_deposits <= 0:
        # No positive denominator, so no ratios: keep the money series so `final_equity`
        # still describes the account (ADR-066).
        legacy = [net_deposits]
        for trade in trades:
            legacy.append(legacy[-1] + float(trade.pnl or 0))
        metrics = compute_metrics(np.asarray(legacy, dtype=float), trade_dicts, timeframe="1d")
        final_equity = legacy[-1]
    else:
        # The ratio series is funding-neutral: every trade is measured against the money
        # it actually ran on, so a deposit or withdrawal moves the baseline instead of
        # being published as a gain (+100% used to come out of "withdraw everything after
        # a profit", ADR-121). The money figures stay money.
        start, opening, flows, _closed = _account_life(db, account)
        # Every closed trade belongs to the current life: a reset deletes them, so there
        # is nothing to filter out here (and the curve's own `start` filter exists to keep
        # funding steps out of the past, not to drop trades).
        index = [1.0]
        equity = opening
        pending = list(flows)
        for trade in trades:
            moment = _aware(trade.exit_time)
            while pending and pending[0][0] <= moment:
                equity += pending.pop(0)[1]
            pnl = float(trade.pnl or 0)
            if equity > 0:
                index.append(index[-1] * (1.0 + pnl / equity))
            equity += pnl
        # One point per trade is not a calendar: annualise over the time the trades
        # actually span, not over "a point means a day" (ADR-121).
        span = _trade_span(trades)
        years = span.total_seconds() / (86_400.0 * 365.25) if span is not None else 0.0
        values = np.asarray(index, dtype=float)
        if years > 0:
            metrics = compute_metrics(
                values, trade_dicts, timeframe="1d", bars_per_year=len(index) / years
            )
        else:
            metrics = compute_metrics(values, trade_dicts, timeframe="1d")
            if metrics.cagr is not None:
                metrics.cagr = None
                metrics.notes.append(
                    "the closed trades span no time, so there is no period to annualise over"
                )
        final_equity = net_deposits + sum(float(t.pnl or 0) for t in trades)
        metrics.initial_capital = net_deposits
        metrics.final_equity = final_equity
    return {
        "account_id": account_id,
        "net_deposits": net_deposits,
        "final_equity": final_equity,
        "closed_trades": len(trades),
        "metrics": metrics.as_dict(),
        # Why a metric is missing is part of the answer: without this the caller only
        # sees `null` and has to guess (ADR-066).
        "metric_notes": metrics.notes,
        "note": (
            "指标由已平仓交易计算；持仓未实现盈亏不计入。收益率是资金中性的"
            "（每笔交易按它实际动用的钱计算），入金/提现只移动基准，不算收益也不算"
            "回撤；年化用交易真实跨越的时间。期末权益 = 净入金（入金 − 提现）+ "
            "已实现盈亏；净入金 ≤ 0 时不发布收益率类指标（ADR-066、ADR-121）。"
        ),
    }


def _trade_payloads(db: Session, rows: list[PaperTrade], *, with_account: bool) -> list[dict]:
    """The trade rows, each one naming the order and the signal behind it (ADR-204).

    A trade stores the order that produced it and an order stores the signal it was
    executed for, so "where did this fill come from?" is answerable from stored facts
    instead of a guess about which strategy it "must have" belonged to. Attribution
    stays per account (ADR-114): this publishes the row's own provenance, not a
    strategy-level join. The signal is read back in one query for the whole page, and
    `None` means the order did not come from a signal — not that it came from one we
    could not find.
    """

    order_ids = [int(t.order_id) for t in rows if t.order_id is not None]
    signal_by_order: dict[int, int | None] = {}
    if order_ids:
        signal_by_order = {
            int(order_id): (int(signal_id) if signal_id is not None else None)
            for order_id, signal_id in db.execute(
                select(PaperOrder.id, PaperOrder.signal_id).where(PaperOrder.id.in_(order_ids))
            ).all()
        }
    payloads: list[dict] = []
    for t in rows:
        order_id = int(t.order_id) if t.order_id is not None else None
        payload: dict = {"id": t.id}
        if with_account:
            payload["account_id"] = t.account_id
        payload.update(
            {
                "asset_id": t.asset_id,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "entry_price": float(t.entry_price),
                "exit_time": t.exit_time,
                "exit_price": float(t.exit_price) if t.exit_price is not None else None,
                "quantity": float(t.quantity),
                "fees": float(t.fees) if t.fees is not None else None,
                "slippage": float(t.slippage) if t.slippage is not None else None,
                "pnl": float(t.pnl) if t.pnl is not None else None,
                "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
                "reason": t.reason,
                "strategy_version": t.strategy_version,
                "order_id": order_id,
                "signal_id": signal_by_order.get(order_id) if order_id is not None else None,
            }
        )
        payloads.append(payload)
    return payloads


@router.get("/accounts/{account_id}/trades", summary="List paper trades")
def account_trades(account_id: int, db: Session = Depends(get_db)) -> list[dict]:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    rows = db.scalars(
        select(PaperTrade).where(PaperTrade.account_id == account_id).order_by(PaperTrade.id)
    ).all()
    return _trade_payloads(db, list(rows), with_account=False)


@router.get(
    "/accounts/{account_id}/positions",
    response_model=list[PaperPositionOut],
    summary="List open positions",
)
def account_positions(account_id: int, db: Session = Depends(get_db)) -> list[PaperPositionOut]:
    """Open positions of an account, each marked at its asset's latest closed bar."""

    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    return [_position_payload(db, row) for row in _account_positions(db, account_id)]


@router.get("/orders", response_model=list[PaperOrderOut], summary="List paper orders")
def list_orders(
    db: Session = Depends(get_db),
    account_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PaperOrder]:
    stmt = select(PaperOrder)
    if account_id is not None:
        stmt = stmt.where(PaperOrder.account_id == account_id)
    return list(db.scalars(stmt.order_by(PaperOrder.id.desc()).limit(limit)).all())


@router.get(
    "/accounts/{account_id}/orders",
    response_model=list[PaperOrderOut],
    summary="List orders for a paper account",
)
def account_orders(
    account_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PaperOrder]:
    if db.get(PaperAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    return list(
        db.scalars(
            select(PaperOrder)
            .where(PaperOrder.account_id == account_id)
            .order_by(PaperOrder.id.desc())
            .limit(limit)
        ).all()
    )


@router.get("/orders/{order_id}", response_model=PaperOrderOut, summary="Get a paper order")
def get_order(order_id: int, db: Session = Depends(get_db)) -> PaperOrder:
    order = db.get(PaperOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="paper order not found")
    return order


@router.get("/trades", summary="List paper trades across accounts")
def list_trades(
    db: Session = Depends(get_db),
    account_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    stmt = select(PaperTrade)
    if account_id is not None:
        stmt = stmt.where(PaperTrade.account_id == account_id)
    rows = db.scalars(stmt.order_by(PaperTrade.id.desc()).limit(limit)).all()
    return _trade_payloads(db, list(rows), with_account=True)


@router.post(
    "/accounts/{account_id}/execute",
    response_model=PaperExecutionOut,
    summary="Execute a persisted signal against a paper account (virtual fills)",
)
def execute(
    account_id: int, payload: PaperExecuteRequest, db: Session = Depends(get_db)
) -> PaperExecutionOut:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    signal = db.get(Signal, payload.signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")
    settings = PaperExecutionSettings(
        fee_bps=payload.fee_bps if payload.fee_bps is not None else 10.0,
        slippage_bps=payload.slippage_bps if payload.slippage_bps is not None else 5.0,
        max_position_pct=(
            payload.max_position_pct if payload.max_position_pct is not None else 1.0
        ),
    )
    try:
        # `quantity` / `notional` are passed through untouched: the engine owns the rule
        # that at most one of them may be given and refuses the rest with a 422, so the
        # API cannot drift from the engine's sizing (ADR-181).
        result = execute_signal(
            db,
            account,
            signal,
            settings=settings,
            quantity=payload.quantity,
            notional=payload.notional,
        )
    except PaperError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PaperExecutionOut(
        account_id=account_id,
        side=result["side"],
        order_id=result["order_id"],
        quantity=float(result["quantity"]),
        fill_price=float(result["fill_price"]),
        fees=float(result["fees"]),
        slippage=float(result["slippage"]),
        realized_pnl=float(result["realized_pnl"]),
        cash=float(account.cash),
    )


@router.get(
    "/accounts/{account_id}/positions/{asset_id}",
    response_model=PaperPositionOut,
    summary="Get a single position",
)
def account_position(
    account_id: int, asset_id: int, db: Session = Depends(get_db)
) -> PaperPositionOut:
    if db.get(PaperAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    position = db.scalar(
        select(PaperPosition).where(
            PaperPosition.account_id == account_id, PaperPosition.asset_id == asset_id
        )
    )
    if position is None:
        raise HTTPException(status_code=404, detail="position not found")
    return _position_payload(db, position)


@router.post("/accounts/{account_id}/close", summary="Close (freeze) a paper account")
def close_account(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    account.status = "closed"
    record_audit(
        db,
        event_type="paper_account_status_changed",
        entity_type="paper_account",
        entity_id=str(account_id),
        action="close",
        payload={"status": "closed"},
    )
    db.commit()
    return {"account_id": account_id, "status": account.status}


@router.post("/accounts/{account_id}/reopen", summary="Reopen a closed paper account")
def reopen_account(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    account.status = "active"
    record_audit(
        db,
        event_type="paper_account_status_changed",
        entity_type="paper_account",
        entity_id=str(account_id),
        action="reopen",
        payload={"status": "active"},
    )
    db.commit()
    return {"account_id": account_id, "status": account.status}


@router.post("/accounts/{account_id}/fund", summary="Add or withdraw virtual cash (audited)")
def fund_account(account_id: int, payload: PaperFundRequest, db: Session = Depends(get_db)) -> dict:
    from decimal import Decimal

    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    amount = Decimal(str(payload.amount))
    if amount == 0:
        raise HTTPException(status_code=422, detail="amount must be non-zero")
    new_cash = Decimal(str(account.cash)) + amount
    if new_cash < 0:
        raise HTTPException(status_code=422, detail="withdrawal exceeds available cash")
    account.cash = new_cash
    # Money entering or leaving the account is not trading performance: the baseline
    # moves with the flow in both directions, so the published P&L is measured against
    # the net deposits the account actually holds (ADR-066). Withdrawing used to lower
    # only `cash`, which showed up as a trading loss and left `final_equity` describing
    # a baseline the money had already left.
    account.initial_cash = Decimal(str(account.initial_cash)) + amount
    record_audit(
        db,
        event_type="paper_account_funded",
        entity_type="paper_account",
        entity_id=str(account_id),
        action="fund",
        payload={
            "amount": str(amount),
            "cash": str(new_cash),
            "net_deposits": str(account.initial_cash),
        },
    )
    db.commit()
    return {
        "account_id": account_id,
        "cash": float(account.cash),
        "net_deposits": float(account.initial_cash),
    }


@router.post("/accounts/{account_id}/reset", summary="Reset a paper account (audited)")
def reset_account(
    account_id: int,
    initial_cash: float | None = None,
    db: Session = Depends(get_db),
) -> dict:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    # `initial_cash or …` treated an explicit 0 as "not given": resetting an account to
    # an empty balance silently restored the old baseline instead.
    target = float(account.initial_cash) if initial_cash is None else initial_cash
    account.cash = target
    account.initial_cash = target
    account.reset_count += 1
    for position in db.scalars(
        select(PaperPosition).where(PaperPosition.account_id == account_id)
    ).all():
        db.delete(position)
    db.query(PaperTrade).filter(PaperTrade.account_id == account_id).delete()
    # Reset promises to clear the account's history, and the orders are part of that
    # history: a P&L that no longer exists must not keep showing up in `GET /paper/orders`
    # as if the trades behind it were still there.
    db.query(PaperOrder).filter(PaperOrder.account_id == account_id).delete()
    record_audit(
        db,
        event_type="paper_account_reset",
        entity_type="paper_account",
        entity_id=str(account_id),
        action="reset",
        payload={"new_cash": target, "reset_count": account.reset_count},
    )
    db.commit()
    return {
        "account_id": account_id,
        "cash": float(account.cash),
        "net_deposits": float(account.initial_cash),
        "reset_count": account.reset_count,
        "warning": (
            "All virtual positions and trades were deleted and the funding baseline was "
            "reset to the new opening cash. This cannot be undone."
        ),
    }
