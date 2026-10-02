"""Strategy ensemble: combine several strategies into one portfolio (docs/24, ADR-046).

The question is not "which strategy is best" but "do strategies that *agree* behave
better than any one of them". Members vote per bar and the combined decision drives a
single portfolio.

Two design constraints shaped this module:

* **Votes must actually be executed.** ``run_backtest`` recomputes its own entry/exit
  flags from the spec, so handing it a "combined spec" would silently discard the
  vote and return a member's result. The ensemble therefore executes the combined
  decision series directly, sharing the engine's fill/cost/exit helpers
  (``_resolve_exit`` / ``_trade_record`` / ``_position_quantity``) and
  ``compute_metrics`` so its semantics match a single-strategy run.
* **One portfolio, not a pile of trade lists.** Concatenating members' trades would
  imply holding several positions at once, which this engine (one position, one cash
  balance) cannot represent honestly.

Members must share a dataset and timeframe; the caller resolves the bars once.
Weights are normalised, so ``[2, 1]`` and ``[0.67, 0.33]`` behave identically.
Members are evaluated on their own feature frame — a spec may declare its own
indicators — and the portfolio trades on the timestamps they have in common.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.features.engine import build_features
from app.research.engine import (
    _cost_multipliers,
    _position_quantity,
    _resolve_exit,
    _trade_record,
    direction_sign,
)
from app.research.metrics import compute_metrics
from app.strategies.dsl import StrategySpec, merge_spec_overrides
from app.strategies.executor import run_strategy

__all__ = ["ENSEMBLE_VERSION", "EnsembleMember", "run_ensemble"]

ENSEMBLE_VERSION = "1.0.0"

# A readable report is the point; beyond a dozen members the per-member attribution
# stops being useful. This also bounds the work (one feature build per member).
MAX_MEMBERS = 12


@dataclass
class EnsembleMember:
    """One strategy version taking part in the vote."""

    label: str
    spec: StrategySpec
    weight: float = 1.0


@dataclass
class _Evaluated:
    label: str
    weight: float
    frame: pd.DataFrame
    entry_long: pd.Series
    exit_long: pd.Series
    entry_short: pd.Series | None = None
    exit_short: pd.Series | None = None
    risk_stop: pd.Series | None = None
    risk_target: pd.Series | None = None
    risk_stop_short: pd.Series | None = None
    risk_target_short: pd.Series | None = None
    tags: list[str] = field(default_factory=list)


def run_ensemble(
    members: list[EnsembleMember],
    bars: pd.DataFrame,
    *,
    vote_threshold: float = 0.5,
    spec_overrides: dict[str, Any] | None = None,
    strategy_version: str = "ensemble",
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Vote members' decisions per bar and backtest the combined portfolio.

    ``spec_overrides`` is merged into the first member's spec (top-level keys, e.g.
    ``{"execution": {...}, "risk": {...}}``) to define how the *portfolio* trades:
    cost model, capital and sizing belong to the portfolio, not to a single member.
    """

    if not members:
        raise ValueError("an ensemble needs at least one member")
    if len(members) > MAX_MEMBERS:
        raise ValueError(f"an ensemble supports at most {MAX_MEMBERS} members")
    if not 0.0 <= vote_threshold < 1.0:
        raise ValueError("vote_threshold must be in [0, 1): the vote must exceed it")

    raw_weights = [float(m.weight) for m in members]
    if any(w < 0 for w in raw_weights):
        raise ValueError("member weights must be >= 0")
    total_weight = sum(raw_weights)
    if total_weight <= 0:
        raise ValueError("member weights must not all be zero")
    weights = [w / total_weight for w in raw_weights]

    warnings: list[str] = []
    evaluated: list[_Evaluated] = []
    for member, weight in zip(members, weights, strict=True):
        feature_frame = build_features(bars, spec=member.spec)
        frame = feature_frame.frame
        out, _ = run_strategy(member.spec, frame)
        evaluated.append(
            _Evaluated(
                label=member.label,
                weight=weight,
                frame=frame,
                entry_long=out["entry_long"].astype(bool),
                exit_long=out["exit_long"].astype(bool),
                entry_short=(out["entry_short"].astype(bool) if "entry_short" in out else None),
                exit_short=(out["exit_short"].astype(bool) if "exit_short" in out else None),
                risk_stop=out.get("risk_stop"),
                risk_target=out.get("risk_target"),
                risk_stop_short=out.get("risk_stop_short"),
                risk_target_short=out.get("risk_target_short"),
            )
        )
        if feature_frame.warmup_bars:
            warnings.append(
                f"member '{member.label}' has a {feature_frame.warmup_bars}-bar warm-up"
            )

    # Every vote is cast on the same bar, so trade on the intersection of timestamps.
    index = evaluated[0].frame.index
    for item in evaluated[1:]:
        index = index.intersection(item.frame.index)
    if len(index) == 0:
        raise ValueError("members share no common bars; check their indicators and warm-up")
    if len(index) < len(evaluated[0].frame.index):
        warnings.append(
            f"ensemble evaluates {len(index)} of {len(evaluated[0].frame.index)} bars "
            "(members' warm-ups overlap only partially)"
        )

    def votes(attr: str) -> pd.Series:
        total = pd.Series(0.0, index=index)
        for item in evaluated:
            series = getattr(item, attr)
            if series is None:
                continue
            total = total + series.reindex(index, fill_value=False).astype(float) * item.weight
        return total

    # Strictly greater than the threshold. With equal weights each member carries
    # exactly 0.5, so an inclusive `>= 0.5` would let ONE member alone clear a
    # "strict majority" and the ensemble would degenerate into a union of members
    # (probe: AND=0 bars but the vote fired 11 — the union). Majority means more than.
    agreed_long = votes("entry_long") > vote_threshold
    agreed_exit = votes("exit_long") > vote_threshold
    any_short = all(item.entry_short is not None for item in evaluated)
    agreed_short = votes("entry_short") > vote_threshold if any_short else None

    # The portfolio needs one cost model and one stop rule. Inheriting the first
    # member's (overridable) is documented behaviour; inventing them would be worse.
    # merge_spec_overrides re-validates, so a nested override such as
    # {"execution": {"sizing": {...}}} actually takes effect instead of being kept as
    # an unvalidated dict (which the engine would silently ignore).
    portfolio_spec = merge_spec_overrides(members[0].spec, spec_overrides)

    execution = portfolio_spec.execution
    risk = portfolio_spec.risk
    max_position_pct = risk.max_position_pct if risk and risk.max_position_pct else 1.0
    fee_rate, slippage_rate = _cost_multipliers(execution.fee_bps, execution.slippage_bps)

    # Risk lines come from the first member that defines them: a single portfolio can
    # carry one stop, and mixing members' stops bar-by-bar would be arbitrary.
    stop_long = pd.Series(np.nan, index=index)
    target_long = pd.Series(np.nan, index=index)
    stop_short = pd.Series(np.nan, index=index)
    target_short = pd.Series(np.nan, index=index)
    for item in evaluated:
        for target_series, source in (
            (stop_long, item.risk_stop),
            (target_long, item.risk_target),
            (stop_short, item.risk_stop_short),
            (target_short, item.risk_target_short),
        ):
            if source is None:
                continue
            line = source.reindex(index)
            target_series.loc[:] = target_series.where(target_series.notna(), line)

    frame = evaluated[0].frame.reindex(index)
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    entry_long = agreed_long.to_numpy(dtype=bool)
    exit_long = agreed_exit.to_numpy(dtype=bool)
    entry_short = agreed_short.to_numpy(dtype=bool) if agreed_short is not None else None
    exit_short = (
        (votes("exit_short") >= vote_threshold).to_numpy(dtype=bool)
        if agreed_short is not None
        else None
    )
    stop_long_v = stop_long.to_numpy(dtype=float)
    target_long_v = target_long.to_numpy(dtype=float)
    stop_short_v = stop_short.to_numpy(dtype=float)
    target_short_v = target_short.to_numpy(dtype=float)

    capital = float(execution.initial_capital)
    cash = capital
    quantity = 0.0
    entry_price = 0.0
    entry_index = -1
    entry_fee = 0.0
    entry_slippage = 0.0
    direction = "LONG"
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    in_position: list[bool] = []
    trade_high = 0.0
    trade_low = 0.0
    entry_stop: float | None = None
    symbol = str(frame["symbol"].iloc[0]) if "symbol" in frame.columns else ""
    # Count entries the engine actually *took*, not bars where the vote fired: while a
    # position is open a new vote is ignored, so signal bars can exceed positions and
    # would overstate agreement relative to a member's own entry count.
    entries_taken = 0

    for i in range(len(frame)):
        bar_time = index[i]
        close = float(closes[i])

        if quantity > 0:
            trade_high = max(trade_high, float(highs[i]))
            trade_low = min(trade_low, float(lows[i]))
            is_long = direction == "LONG"
            stop = stop_long_v[i] if is_long else stop_short_v[i]
            target = target_long_v[i] if is_long else target_short_v[i]
            rule_exit = bool(exit_long[i] if is_long else exit_short[i])
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
                fill = exit_price - slip if is_long else exit_price + slip
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

        # Entry fills at the *next* bar open, matching the single-strategy engine.
        if quantity == 0 and i + 1 < len(frame):
            want_long = bool(entry_long[i])
            want_short = bool(entry_short[i]) if entry_short is not None else False
            if want_long or want_short:
                fill_ref = float(opens[i + 1])
                slip = fill_ref * slippage_rate
                fill = fill_ref + slip if want_long else fill_ref - slip
                raw_stop = stop_long_v[i] if want_long else stop_short_v[i]
                stop_distance = (
                    abs(fill - float(raw_stop)) if not np.isnan(float(raw_stop)) else None
                )
                qty = _position_quantity(
                    sizing=execution.sizing,
                    cash=cash,
                    fill=fill,
                    max_position_pct=float(max_position_pct),
                    stop_distance=stop_distance,
                    allow_fractional=execution.allow_fractional,
                )
                if qty > 0:
                    fee = abs(fill * qty) * fee_rate
                    entries_taken += 1
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
                    entry_stop = None if np.isnan(float(raw_stop)) else float(raw_stop)

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
    metrics = compute_metrics(
        equity_values if len(equity_values) else np.array([capital]), trades, timeframe=timeframe
    )

    member_summary = [
        {
            "label": item.label,
            "weight": item.weight,
            "entry_bars": int(item.entry_long.reindex(index, fill_value=False).sum()),
            "exit_bars": int(item.exit_long.reindex(index, fill_value=False).sum()),
        }
        for item in evaluated
    ]

    return {
        "ensemble_version": ENSEMBLE_VERSION,
        "vote_threshold": vote_threshold,
        "members": member_summary,
        "bars_evaluated": len(index),
        "agreement": {
            "entry_bars": int(entry_long.sum()),
            "exit_bars": int(exit_long.sum()),
            "short_entry_bars": int(entry_short.sum()) if entry_short is not None else 0,
            # Bars where the vote fired AND the portfolio was flat, i.e. positions
            # actually opened. This is the number comparable to a member's entries.
            "entries_taken": entries_taken,
        },
        "metrics": metrics.as_dict(),
        "trades": trades,
        "equity_curve": equity_curve,
        "final_equity": float(equity_values[-1]) if len(equity_values) else capital,
        "initial_capital": capital,
        "engine_version": "ensemble-1.0.0",
        "feature_version": "ensemble",
        "warnings": warnings,
    }
