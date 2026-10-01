"""Deterministic backtest engine.

Timing contract (docs/07_BACKTEST_ENGINE.md):

    bar t closes
      -> evaluate the strategy using information up to and including bar t
      -> the order becomes eligible
      -> fill at bar t+1 open (default)

The engine therefore never reads a future bar to make a decision. When a stop
loss and a take profit would both trigger inside the same bar, the *pessimistic*
(conservative) fill is applied and the trade is flagged ``ambiguous_fill``.

The same ``strategy version + dataset + parameters + engine version + feature
version`` always produces the same numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.features.engine import FEATURE_VERSION, build_features, feature_input_hash
from app.research.metrics import compute_metrics
from app.strategies.dsl import StrategySpec
from app.strategies.executor import run_strategy

__all__ = ["BacktestResult", "ENGINE_VERSION", "run_backtest"]

ENGINE_VERSION = "1.0.0"
BARS_PER_YEAR_DEFAULT = 252.0


@dataclass
class BacktestResult:
    strategy_version: str
    engine_version: str
    feature_version: str
    dataset_hash: str
    timeframe: str
    initial_capital: float
    final_equity: float
    metrics: dict[str, Any]
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    result_hash: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_version": self.strategy_version,
            "engine_version": self.engine_version,
            "feature_version": self.feature_version,
            "dataset_hash": self.dataset_hash,
            "timeframe": self.timeframe,
            "initial_capital": self.initial_capital,
            "final_equity": self.final_equity,
            "metrics": self.metrics,
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "signals": self.signals,
            "result_hash": self.result_hash,
            "warnings": self.warnings,
        }


def _cost_multipliers(fee_bps: float, slippage_bps: float) -> tuple[float, float]:
    return fee_bps / 10_000.0, slippage_bps / 10_000.0


def run_backtest(
    spec: StrategySpec,
    bars: pd.DataFrame,
    *,
    strategy_version: str = "unversioned",
    timeframe: str = "1d",
    parameters: dict[str, Any] | None = None,
) -> BacktestResult:
    """Run a long-only (or short-enabled) backtest over ``bars``.

    ``bars`` must contain ``timestamp`` (UTC) plus OHLCV columns.
    """

    parameters = parameters or {}
    warnings: list[str] = []
    fee_rate, slippage_rate = _cost_multipliers(spec.execution.fee_bps, spec.execution.slippage_bps)
    capital = float(spec.execution.initial_capital)
    max_position_pct = spec.risk.max_position_pct if spec.risk else None
    if max_position_pct is None:
        max_position_pct = 1.0
    symbol = str(bars["symbol"].iloc[0]) if "symbol" in bars.columns else ""

    feature_frame = build_features(bars, spec=spec)
    frame = feature_frame.frame
    dataset_hash = feature_input_hash(bars)

    decisions, _ = run_strategy(spec, frame)

    if feature_frame.warmup_bars and len(frame) <= feature_frame.warmup_bars:
        warnings.append(
            f"only {len(frame)} bars available, warm-up needs {feature_frame.warmup_bars}"
        )

    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    index = frame.index

    entry_flag = decisions["entry_long"].to_numpy(dtype=bool)
    exit_flag = decisions["exit_long"].to_numpy(dtype=bool)
    entry_short_flag = (
        decisions["entry_short"].to_numpy(dtype=bool)
        if "entry_short" in decisions
        else np.zeros(len(frame), dtype=bool)
    )
    exit_short_flag = (
        decisions["exit_short"].to_numpy(dtype=bool)
        if "exit_short" in decisions
        else np.zeros(len(frame), dtype=bool)
    )
    stop_line = decisions["risk_stop"].to_numpy(dtype=float)
    target_line = decisions["risk_target"].to_numpy(dtype=float)
    if "risk_stop_short" in decisions:
        stop_line = np.where(np.isnan(stop_line), np.nan, stop_line)
        stop_short_line = decisions["risk_stop_short"].to_numpy(dtype=float)
        target_short_line = decisions["risk_target_short"].to_numpy(dtype=float)
    else:
        stop_short_line = np.full(len(frame), np.nan)
        target_short_line = np.full(len(frame), np.nan)

    cash = capital
    quantity = 0.0
    entry_price = 0.0
    entry_index = -1
    entry_fee = 0.0
    entry_slippage = 0.0
    direction = "LONG"
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    in_position: list[bool] = []
    trade_high = 0.0
    trade_low = 0.0
    entry_stop: float | None = None

    for i in range(len(frame)):
        bar_time = index[i]
        close = float(closes[i])

        # 1) Manage an open position with the *current* bar's extremes.
        if quantity > 0:
            # Track the best/worst prices seen during this trade for MAE/MFE.
            trade_high = max(trade_high, float(highs[i]))
            trade_low = min(trade_low, float(lows[i]))

            is_long = direction == "LONG"
            # A short position mirrors the levels: its stop sits above entry and
            # its target below, so the raw close/ATR lines are inverted.
            stop = stop_line[i] if is_long else stop_short_line[i]
            target = target_line[i] if is_long else target_short_line[i]
            rule_exit = bool(exit_flag[i] if is_long else exit_short_flag[i])
            exit_price, reason, ambiguous = _resolve_exit(
                bar_high=float(highs[i]),
                bar_low=float(lows[i]),
                stop=stop,
                target=target,
                close=close,
                rule_exit=rule_exit,
                is_long=is_long,
            )
            if exit_price is not None:
                slip = exit_price * slippage_rate
                fill = exit_price - slip if direction == "LONG" else exit_price + slip
                fee = abs(fill * quantity) * fee_rate
                cash += direction_sign(direction) * (fill * quantity) - fee
                pnl = (fill - entry_price) * quantity * direction_sign(direction) - fee - entry_fee
                trades.append(
                    _trade_record(
                        direction=direction,
                        symbol=symbol,
                        entry_time=index[entry_index],
                        entry_price=entry_price,
                        exit_time=bar_time,
                        exit_price=fill,
                        quantity=quantity,
                        fees=fee + entry_fee,
                        slippage=abs(slip) + abs(entry_slippage),
                        pnl=pnl,
                        holding_bars=i - entry_index,
                        exit_reason=reason,
                        ambiguous_fill=ambiguous,
                        strategy_version=strategy_version,
                        trade_high=trade_high,
                        trade_low=trade_low,
                        entry_stop=entry_stop,
                    )
                )
                quantity = 0.0
                entry_price = 0.0
                entry_fee = 0.0
                entry_slippage = 0.0
                entry_stop = None
                direction = "LONG"
                signals.append(
                    {"bar_time": bar_time.isoformat(), "state": "SELL", "direction": "FLAT"}
                )

        # 2) Open a new position on the *next* bar open.
        if quantity == 0 and i + 1 < len(frame):
            want_long = bool(entry_flag[i]) and not _warmup(i, feature_frame.warmup_bars)
            want_short = bool(entry_short_flag[i]) and spec.market.allow_short
            if want_long or want_short:
                fill_ref = float(opens[i + 1])
                slip = fill_ref * slippage_rate
                fill = fill_ref + slip if want_long else fill_ref - slip
                budget = cash * float(max_position_pct)
                if budget > 0:
                    qty = budget / fill
                    if not spec.execution.allow_fractional:
                        qty = float(int(qty))
                    if qty > 0:
                        fee = abs(fill * qty) * fee_rate
                        if want_long:
                            cash -= fill * qty + fee
                            direction = "LONG"
                        else:
                            cash += fill * qty - fee
                            direction = "SHORT"
                        quantity = qty
                        entry_price = fill
                        entry_fee = fee
                        entry_slippage = abs(slip)
                        entry_index = i + 1
                        trade_high = fill
                        trade_low = fill
                        raw_stop = stop_line[i] if want_long else stop_short_line[i]
                        entry_stop = float(raw_stop) if not np.isnan(float(raw_stop)) else None
                        signals.append(
                            {
                                "bar_time": index[i].isoformat(),
                                "state": "BUY" if want_long else "SELL",
                                "direction": direction,
                                "fill_time": index[i + 1].isoformat(),
                                "fill_price": fill,
                            }
                        )

        # 3) Mark to market.
        position_value = quantity * close * direction_sign(direction)
        in_position.append(quantity > 0)
        equity_curve.append(
            {
                "timestamp": bar_time.isoformat(),
                "equity": cash + position_value,
                "cash": cash,
                "position_value": position_value,
                "close": close,
            }
        )

    # Close any still-open position at the final close so results are comparable.
    if quantity > 0 and len(frame) > 0:
        last = len(frame) - 1
        fill = float(closes[last])
        fee = abs(fill * quantity) * fee_rate
        cash += direction_sign(direction) * (fill * quantity) - fee
        pnl = (fill - entry_price) * quantity * direction_sign(direction) - fee - entry_fee
        trades.append(
            _trade_record(
                direction=direction,
                symbol=symbol,
                entry_time=index[entry_index],
                entry_price=entry_price,
                exit_time=index[last],
                exit_price=fill,
                quantity=quantity,
                fees=fee + entry_fee,
                slippage=entry_slippage,
                pnl=pnl,
                holding_bars=last - entry_index,
                exit_reason="end_of_data",
                ambiguous_fill=False,
                strategy_version=strategy_version,
                trade_high=trade_high,
                trade_low=trade_low,
                entry_stop=entry_stop,
            )
        )
        equity_curve[-1]["equity"] = cash
        equity_curve[-1]["cash"] = cash
        equity_curve[-1]["position_value"] = 0.0

    equity_values = np.array([point["equity"] for point in equity_curve], dtype=float)
    metrics = compute_metrics(equity_values, trades, timeframe=timeframe, bars_per_year=None)
    metrics.exposure = _exposure(in_position)
    metrics.turnover = _turnover(trades, float(capital))

    payload = json.dumps(
        {
            "strategy_version": strategy_version,
            "dataset_hash": dataset_hash,
            "engine_version": ENGINE_VERSION,
            "feature_version": FEATURE_VERSION,
            "parameters": parameters,
            "metrics": metrics.as_dict(),
            "trade_count": len(trades),
        },
        sort_keys=True,
        default=str,
    )
    result_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return BacktestResult(
        strategy_version=strategy_version,
        engine_version=ENGINE_VERSION,
        feature_version=FEATURE_VERSION,
        dataset_hash=dataset_hash,
        timeframe=timeframe,
        initial_capital=capital,
        final_equity=float(equity_values[-1]) if len(equity_values) else capital,
        metrics=metrics.as_dict(),
        trades=trades,
        equity_curve=equity_curve,
        signals=signals,
        result_hash=result_hash,
        warnings=warnings,
    )


def direction_sign(direction: str) -> float:
    return -1.0 if direction == "SHORT" else 1.0


def _warmup(index: int, warmup_bars: int) -> bool:
    return index < warmup_bars


def _resolve_exit(
    *,
    bar_high: float,
    bar_low: float,
    stop: float,
    target: float,
    close: float,
    rule_exit: bool,
    is_long: bool = True,
) -> tuple[float | None, str, bool]:
    """Resolve an exit price for the current bar.

    When a stop and a target are both inside the same bar the *pessimistic*
    (conservative) level is used and the trade is flagged ``ambiguous_fill``: the
    engine must never pick the order that flatters the result.
    """

    has_stop = not np.isnan(stop)
    has_target = not np.isnan(target)
    if is_long:
        stop_hit = has_stop and bar_low <= stop
        target_hit = has_target and bar_high >= target
    else:
        stop_hit = has_stop and bar_high >= stop
        target_hit = has_target and bar_low <= target

    if stop_hit and target_hit:
        return float(stop), "stop_loss", True
    if stop_hit:
        return float(stop), "stop_loss", False
    if target_hit:
        return float(target), "take_profit", False
    if rule_exit:
        return close, "rule_exit", False
    return None, "", False


def _trade_record(
    *,
    direction: str,
    symbol: str,
    entry_time: Any,
    entry_price: float,
    exit_time: Any,
    exit_price: float,
    quantity: float,
    fees: float,
    slippage: float,
    pnl: float,
    holding_bars: int,
    exit_reason: str,
    ambiguous_fill: bool,
    strategy_version: str,
    trade_high: float = 0.0,
    trade_low: float = 0.0,
    entry_stop: float | None = None,
) -> dict[str, Any]:
    pnl_pct = (exit_price - entry_price) / entry_price * direction_sign(direction)

    # MAE = maximum adverse excursion (worst move against the position)
    # MFE = maximum favourable excursion (best move in favour)
    if direction == "LONG":
        mae = round(max(0.0, entry_price - trade_low), 8) if trade_low else None
        mfe = round(max(0.0, trade_high - entry_price), 8) if trade_high else None
    else:
        mae = round(max(0.0, trade_high - entry_price), 8) if trade_high else None
        mfe = round(max(0.0, entry_price - trade_low), 8) if trade_low else None

    # R multiple = actual PnL / initial dollar risk (entry to stop distance × qty)
    r_multiple = None
    if entry_stop is not None and quantity > 0:
        risk_per_unit = abs(entry_price - entry_stop)
        if risk_per_unit > 0:
            r_multiple = round(pnl / (risk_per_unit * quantity), 4)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry_time": entry_time.isoformat() if hasattr(entry_time, "isoformat") else entry_time,
        "entry_price": round(entry_price, 8),
        "exit_time": exit_time.isoformat() if hasattr(exit_time, "isoformat") else exit_time,
        "exit_price": round(exit_price, 8),
        "quantity": round(quantity, 10),
        "fees": round(fees, 8),
        "slippage": round(slippage, 8),
        "pnl": round(pnl, 8),
        "pnl_pct": round(pnl_pct, 8),
        "r_multiple": r_multiple,
        "holding_bars": holding_bars,
        "exit_reason": exit_reason,
        "ambiguous_fill": ambiguous_fill,
        "strategy_version": strategy_version,
        "mae": mae,
        "mfe": mfe,
    }


def _exposure(in_position: list[bool]) -> float | None:
    """Fraction of bars during which a position was held."""
    if not in_position:
        return None
    return float(sum(1 for flag in in_position if flag) / len(in_position))


def _turnover(trades: list[dict[str, Any]], capital: float) -> float | None:
    if not trades or capital <= 0:
        return None
    total = sum(float(t["entry_price"]) * float(t["quantity"]) for t in trades)
    return total / capital
