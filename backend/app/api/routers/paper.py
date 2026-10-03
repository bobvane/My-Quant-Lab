"""Paper trading endpoints.

Paper accounts are **completely isolated** from the real portfolio: they live in
their own tables, are funded with virtual cash and can never write to Ghostfolio.
"""

from __future__ import annotations

import datetime as dt
import logging

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
from app.data.strategy_service import record_audit
from app.domain.models import AuditLog, PaperAccount, PaperOrder, PaperPosition, PaperTrade, Signal
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


def _account_payload(
    db: Session, account: PaperAccount, *, realized: float | None = None
) -> PaperAccountOut:
    if realized is None:
        realized = _realized_by_account(db, [account.id]).get(account.id, 0.0)
    return PaperAccountOut.model_validate(account).model_copy(update={"realized_pnl": realized})


@router.get("/accounts", response_model=list[PaperAccountOut], summary="List paper accounts")
def list_accounts(db: Session = Depends(get_db)) -> list[PaperAccountOut]:
    rows = db.scalars(select(PaperAccount).order_by(PaperAccount.id)).all()
    realized = _realized_by_account(db, [row.id for row in rows])
    return [_account_payload(db, row, realized=realized.get(row.id, 0.0)) for row in rows]


@router.post(
    "/accounts", response_model=PaperAccountOut, status_code=201, summary="Create paper account"
)
def create_account(payload: PaperAccountCreate, db: Session = Depends(get_db)) -> PaperAccountOut:
    account = PaperAccount(
        name=payload.name,
        strategy_id=payload.strategy_id,
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
        payload={"name": account.name, "net_deposits": str(account.initial_cash)},
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


@router.get("/accounts/{account_id}/trades", summary="List paper trades")
def account_trades(account_id: int, db: Session = Depends(get_db)) -> list[dict]:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    rows = db.scalars(
        select(PaperTrade).where(PaperTrade.account_id == account_id).order_by(PaperTrade.id)
    ).all()
    return [
        {
            "id": t.id,
            "asset_id": t.asset_id,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": float(t.quantity),
            "pnl": float(t.pnl) if t.pnl is not None else None,
            "reason": t.reason,
            "strategy_version": t.strategy_version,
        }
        for t in rows
    ]


@router.get(
    "/accounts/{account_id}/positions",
    response_model=list[PaperPositionOut],
    summary="List open positions",
)
def account_positions(account_id: int, db: Session = Depends(get_db)) -> list[PaperPosition]:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    return list(
        db.scalars(
            select(PaperPosition)
            .where(PaperPosition.account_id == account_id)
            .order_by(PaperPosition.id)
        ).all()
    )


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
    return [
        {
            "id": t.id,
            "account_id": t.account_id,
            "asset_id": t.asset_id,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": float(t.quantity),
            "pnl": float(t.pnl) if t.pnl is not None else None,
            "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
            "reason": t.reason,
            "strategy_version": t.strategy_version,
        }
        for t in rows
    ]


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
        result = execute_signal(db, account, signal, settings=settings)
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
) -> PaperPosition:
    if db.get(PaperAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    position = db.scalar(
        select(PaperPosition).where(
            PaperPosition.account_id == account_id, PaperPosition.asset_id == asset_id
        )
    )
    if position is None:
        raise HTTPException(status_code=404, detail="position not found")
    return position


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
    target = initial_cash or float(account.initial_cash)
    account.cash = target
    account.initial_cash = target
    account.reset_count += 1
    for position in db.scalars(
        select(PaperPosition).where(PaperPosition.account_id == account_id)
    ).all():
        db.delete(position)
    db.query(PaperTrade).filter(PaperTrade.account_id == account_id).delete()
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
