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

Sizing: by default an order is all-in — a BUY deploys the whole cash balance (times
``max_position_pct``) and a SELL closes the whole position. A caller may instead name
an exact ``quantity`` or a ``notional`` (gross trade value), because "always all-in" is
not a decision a manual trade can express (ADR-181). Explicit sizing is filled as
asked; the cash guard still binds, because a size the account cannot pay for is not a
size.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
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

# `PaperPosition.quantity` and `PaperTrade.quantity` are Numeric(24, 10): a quantity
# the account cannot hold is a quantity the ledger would round anyway, so the cash
# guard has to run on the rounded value, not on the raw 28-digit quotient. The raw
# quotient of a full-size buy overshoots the balance by one unit in the last place,
# which used to reject orders the cash could cover (ADR-122).
_QUANTITY_SCALE = Decimal("1E-10")


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


def _aware(moment: dt.datetime) -> dt.datetime:
    """SQLite hands back naive timestamps for tz-aware columns."""

    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)


def _fill_moment(signal: Signal, now: dt.datetime | None) -> dt.datetime:
    """The bar the signal was decided on, unless the caller names the moment itself.

    The fill price is that bar's close, so the default fill time has to be that bar
    too: stamping it with ``datetime.now()`` put a January trade on today's date and
    the equity replay (which orders by that stamp) inserted it into the middle of
    history (ADR-123).
    """

    return now if now is not None else _aware(signal.bar_timestamp)


def execute_signal(
    db: Session,
    account: PaperAccount,
    signal: Signal,
    *,
    settings: PaperExecutionSettings | None = None,
    now: dt.datetime | None = None,
    quantity: Decimal | None = None,
    notional: Decimal | None = None,
) -> dict[str, Any]:
    """Execute ``signal`` against ``account`` and persist the result.

    ``quantity`` (units) and ``notional`` (gross trade value) ask for an exact size and
    are mutually exclusive; both default to ``None``, which keeps the historical all-in
    sizing so every existing caller behaves exactly as before (ADR-181).
    """

    limits = settings or PaperExecutionSettings()
    moment = _fill_moment(signal, now)

    # The refusals below keep their order and wording: callers (and the API's 422
    # messages) already depend on them, so a new reason to refuse must come after them.
    if account.status != "active":
        raise PaperError("paper account is not active")
    if signal.state not in {"BUY", "SELL"}:
        raise PaperError(f"signal state {signal.state} is not executable")
    if signal.price_reference is None:
        raise PaperError("signal has no price_reference to fill at")
    if quantity is not None and notional is not None:
        raise PaperError("specify either quantity or notional, not both")

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
            db,
            account,
            signal,
            base_price,
            slip,
            fee_rate,
            limits,
            moment,
            version_label,
            quantity=quantity,
            notional=notional,
        )
    else:
        result = _close_long(
            db,
            account,
            signal,
            base_price,
            slip,
            fee_rate,
            moment,
            position,
            quantity=quantity,
            notional=notional,
        )
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


def _requested_quantity(
    quantity: Decimal | None, notional: Decimal | None, price: Decimal
) -> Decimal | None:
    """The units an explicit order asks for, or ``None`` for the engine's own sizing.

    ``notional`` is converted at the *fill* price, so the caller's amount is what the
    order is worth rather than what the signal's close was worth — the difference is the
    slippage the fill actually pays. Both paths round down at the ledger's scale
    (Numeric(24, 10)) before anything is priced: a size the ledger cannot store is a size
    the account cannot hold, and rounding *after* the cash guard is how the guard used to
    reject orders the balance could cover (ADR-122).
    """

    if price <= 0:
        raise PaperError("invalid fill price")
    if quantity is not None:
        requested = _dec(quantity).quantize(_QUANTITY_SCALE, rounding=ROUND_DOWN)
        if requested <= 0:
            raise PaperError("quantity must be positive")
        return requested
    if notional is not None:
        requested = (_dec(notional) / price).quantize(_QUANTITY_SCALE, rounding=ROUND_DOWN)
        if requested <= 0:
            raise PaperError("notional is too small to fill")
        return requested
    return None


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
    *,
    quantity: Decimal | None = None,
    notional: Decimal | None = None,
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
    if fill_price <= 0:
        raise PaperError("invalid fill price")
    explicit = _requested_quantity(quantity, notional, fill_price)
    if explicit is None:
        budget = _dec(account.cash) * _dec(limits.max_position_pct)
        quantity = (budget / (fill_price * (1 + fee_rate))).quantize(
            _QUANTITY_SCALE, rounding=ROUND_DOWN
        )
    else:
        # An explicit size is filled as asked and is deliberately not scaled by
        # `max_position_pct`: the caller named the size, and quietly shrinking it would
        # persist a different order than the one confirmed. The cash guard below still
        # binds — a size the account cannot pay for is not a size (ADR-181).
        if notional is not None and _dec(notional) > _dec(account.cash):
            raise PaperError("notional exceeds available cash")
        quantity = explicit
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
    *,
    quantity: Decimal | None = None,
    notional: Decimal | None = None,
) -> dict[str, Any]:
    if position is None or _dec(position.quantity) <= 0:
        raise PaperError("no open position to sell")

    held = _dec(position.quantity)
    avg_cost = _dec(position.avg_cost)
    fill_price = base_price * (1 - slip)
    explicit = _requested_quantity(quantity, notional, fill_price)
    # Without explicit sizing the whole position is sold, which is what a signal exit
    # means; a manual SELL may name a smaller size (ADR-181).
    quantity = held if explicit is None else explicit
    if quantity > held:
        raise PaperError("cannot sell more than the open position")
    partial = quantity < held
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
    entry_quantity = _dec(open_trade.quantity) if open_trade is not None else Decimal(0)
    # The open trade carries the entry's fees for the *whole* position. A partial close
    # realises only its slice of that entry, so the slice is charged the share of the
    # buy fee it used: charging the whole entry fee to the first slice would make every
    # partial exit look worse than the position really was, and would leave the
    # remaining slice carrying no entry cost at all. A full close keeps the whole fee,
    # exactly as before.
    slice_share = quantity / entry_quantity if partial and entry_quantity > 0 else Decimal(1)
    buy_fee = (_dec(open_trade.fees) if open_trade is not None else Decimal(0)) * slice_share
    entry_slippage = (
        _dec(open_trade.slippage) if open_trade is not None else Decimal(0)
    ) * slice_share
    pnl = proceeds - (quantity * avg_cost + buy_fee)

    account.cash = _dec(account.cash) + proceeds
    position.realized_pnl = _dec(position.realized_pnl) + pnl
    position.quantity = held - quantity

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
    if open_trade is not None and partial:
        # A partial close realises a slice, so the closed row is the slice and the open
        # row keeps the rest of the entry. Trades then still sum to the position's
        # realised P&L instead of the slice vanishing from the trade history. Without an
        # open trade row there is no entry to attribute (a position may exist without
        # one), so the order and `position.realized_pnl` carry the slice alone.
        db.add(
            PaperTrade(
                account_id=account.id,
                order_id=order.id,
                asset_id=signal.asset_id,
                direction="LONG",
                entry_time=open_trade.entry_time,
                entry_price=open_trade.entry_price,
                exit_time=moment,
                exit_price=fill_price,
                quantity=quantity,
                fees=buy_fee + fees,
                slippage=entry_slippage,
                pnl=pnl,
                reason=open_trade.reason,
                strategy_version=open_trade.strategy_version,
            )
        )
        open_trade.quantity = entry_quantity - quantity
        open_trade.fees = _dec(open_trade.fees) - buy_fee
        open_trade.slippage = _dec(open_trade.slippage) - entry_slippage
    elif open_trade is not None:
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
