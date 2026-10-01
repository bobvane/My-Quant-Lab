"""Deterministic paper-trading execution (docs/08_PAPER_TRADING.md).

Paper accounts are fully isolated from the real portfolio: they live in their
own tables, are funded with virtual cash, and nothing here ever writes to
Ghostfolio or a broker.

V1 is **long-only, one position per asset**. Fills use the signal's reference
price (the close of the bar the signal was computed on) plus configurable
slippage and fees. The engine only reads the already-persisted signal row, so it
cannot introduce lookahead: the decision was made on a closed bar.

Accounting contract:

* BUY  : cash -= qty * fill + fee ; position.avg_cost = fill ; a PaperTrade is
  opened (entry only).
* SELL : proceeds = qty * fill - fee ; cash += proceeds ; the open PaperTrade is
  closed with ``pnl = proceeds - (qty * avg_cost + buy_fee)`` — i.e. the realised
  P&L already nets both sides' fees. A SELL with no open position is refused.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.strategy_service import record_audit
from app.domain.models import (
    PaperAccount,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    Signal,
    StrategyVersion,
)

__all__ = ["PaperError", "PaperExecutionSettings", "execute_signal"]

_BPS = Decimal(10_000)


class PaperError(ValueError):
    """Raised when a paper order cannot be executed as requested."""


@dataclass(frozen=True)
class PaperExecutionSettings:
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    max_position_pct: float = 1.0


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _fee_rate(bps: float) -> Decimal:
    return _dec(bps) / _BPS


def execute_signal(
    db: Session,
    account: PaperAccount,
    signal: Signal,
    *,
    settings: PaperExecutionSettings | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Execute ``signal`` against ``account`` and persist the result."""

    limits = settings or PaperExecutionSettings()
    moment = now or dt.datetime.now(tz=dt.UTC)

    if account.status != "active":
        raise PaperError("paper account is not active")
    if signal.state not in {"BUY", "SELL"}:
        raise PaperError(f"signal state {signal.state} is not executable")
    if signal.price_reference is None:
        raise PaperError("signal has no price_reference to fill at")

    base_price = _dec(signal.price_reference)
    slip = _fee_rate(limits.slippage_bps)
    fee_rate = _fee_rate(limits.fee_bps)

    position = db.scalar(
        select(PaperPosition).where(
            PaperPosition.account_id == account.id,
            PaperPosition.asset_id == signal.asset_id,
        )
    )
    version = db.get(StrategyVersion, signal.strategy_version_id)
    version_label = f"{version.strategy_id}@{version.version}" if version is not None else None

    if signal.state == "BUY":
        result = _open_long(
            db, account, signal, base_price, slip, fee_rate, limits, moment, version_label
        )
    else:
        result = _close_long(db, account, signal, base_price, slip, fee_rate, moment, position)
    record_audit(
        db,
        event_type="paper_order_executed",
        entity_type="paper_account",
        entity_id=str(account.id),
        action="execute",
        payload={
            "side": result["side"],
            "asset_id": signal.asset_id,
            "signal_id": signal.id,
            "quantity": str(result["quantity"]),
            "fill_price": str(result["fill_price"]),
            "fees": str(result["fees"]),
            "slippage": str(result["slippage"]),
        },
    )
    db.commit()
    return result


def _open_long(
    db: Session,
    account: PaperAccount,
    signal: Signal,
    base_price: Decimal,
    slip: Decimal,
    fee_rate: Decimal,
    limits: PaperExecutionSettings,
    moment: dt.datetime,
    version_label: str | None,
) -> dict[str, Any]:
    # A closed position keeps its row (quantity 0) so its realised P&L survives;
    # a later BUY must reuse that row, never insert a second one for the same
    # (account, asset) — the table has a unique constraint on that pair.
    existing = db.scalar(
        select(PaperPosition).where(
            PaperPosition.account_id == account.id,
            PaperPosition.asset_id == signal.asset_id,
        )
    )
    if existing is not None and _dec(existing.quantity) > 0:
        raise PaperError("already holding this asset (V1 is long-only)")

    fill_price = base_price * (1 + slip)
    budget = _dec(account.cash) * _dec(limits.max_position_pct)
    if fill_price <= 0:
        raise PaperError("invalid fill price")
    quantity = budget / (fill_price * (1 + fee_rate))
    if quantity <= 0:
        raise PaperError("insufficient cash")
    fees = quantity * fill_price * fee_rate
    cash_out = quantity * fill_price + fees
    if cash_out > _dec(account.cash):
        raise PaperError("insufficient cash")

    account.cash = _dec(account.cash) - cash_out
    if existing is None:
        db.add(
            PaperPosition(
                account_id=account.id,
                asset_id=signal.asset_id,
                quantity=quantity,
                avg_cost=fill_price,
                realized_pnl=Decimal(0),
            )
        )
    else:
        existing.quantity = quantity
        existing.avg_cost = fill_price
    order = PaperOrder(
        account_id=account.id,
        signal_id=signal.id,
        asset_id=signal.asset_id,
        side="BUY",
        quantity=quantity,
        status="filled",
        reason="signal",
        filled_at=moment,
        fill_price=fill_price,
        fees=fees,
        slippage=quantity * base_price * slip,
    )
    db.add(order)
    db.flush()
    db.add(
        PaperTrade(
            account_id=account.id,
            order_id=order.id,
            asset_id=signal.asset_id,
            direction="LONG",
            entry_time=moment,
            entry_price=fill_price,
            quantity=quantity,
            fees=fees,
            slippage=quantity * base_price * slip,
            reason="signal",
            strategy_version=version_label,
        )
    )
    db.flush()
    return {
        "side": "BUY",
        "order_id": order.id,
        "quantity": quantity,
        "fill_price": fill_price,
        "fees": fees,
        "slippage": quantity * base_price * slip,
        "realized_pnl": Decimal(0),
    }


def _close_long(
    db: Session,
    account: PaperAccount,
    signal: Signal,
    base_price: Decimal,
    slip: Decimal,
    fee_rate: Decimal,
    moment: dt.datetime,
    position: PaperPosition | None,
) -> dict[str, Any]:
    if position is None or _dec(position.quantity) <= 0:
        raise PaperError("no open position to sell")

    quantity = _dec(position.quantity)
    avg_cost = _dec(position.avg_cost)
    fill_price = base_price * (1 - slip)
    gross = quantity * fill_price
    fees = gross * fee_rate
    proceeds = gross - fees

    open_trade = db.scalar(
        select(PaperTrade)
        .where(
            PaperTrade.account_id == account.id,
            PaperTrade.asset_id == signal.asset_id,
            PaperTrade.exit_time.is_(None),
        )
        .order_by(PaperTrade.id.desc())
    )
    buy_fee = _dec(open_trade.fees) if open_trade is not None else Decimal(0)
    pnl = proceeds - (quantity * avg_cost + buy_fee)

    account.cash = _dec(account.cash) + proceeds
    position.realized_pnl = _dec(position.realized_pnl) + pnl
    position.quantity = Decimal(0)

    order = PaperOrder(
        account_id=account.id,
        signal_id=signal.id,
        asset_id=signal.asset_id,
        side="SELL",
        quantity=quantity,
        status="filled",
        reason="signal",
        filled_at=moment,
        fill_price=fill_price,
        fees=fees,
        slippage=quantity * base_price * slip,
    )
    db.add(order)
    db.flush()
    if open_trade is not None:
        open_trade.exit_time = moment
        open_trade.exit_price = fill_price
        open_trade.pnl = pnl
        open_trade.fees = _dec(open_trade.fees) + fees
    db.flush()
    return {
        "side": "SELL",
        "order_id": order.id,
        "quantity": quantity,
        "fill_price": fill_price,
        "fees": fees,
        "slippage": quantity * base_price * slip,
        "realized_pnl": pnl,
    }
