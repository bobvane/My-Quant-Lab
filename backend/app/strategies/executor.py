"""Deterministic strategy executor.

The executor turns a validated :class:`StrategySpec` into a boolean entry/exit
series over a feature frame. It performs **no** I/O, no DB access, no LLM calls
and no network access: input in, structured signal intent out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.strategies.dsl import Condition, ConditionGroup, StrategySpec

__all__ = ["SignalIntent", "evaluate_group", "run_strategy"]


@dataclass(frozen=True)
class SignalIntent:
    """Structured strategy output. AI may only *explain* this, never alter it."""

    state: str
    direction: str
    bar_time: pd.Timestamp | None
    triggered_rules: list[str] = field(default_factory=list)
    price_reference: float | None = None
    stop_reference: float | None = None
    target_reference: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "direction": self.direction,
            "bar_time": self.bar_time.isoformat() if self.bar_time is not None else None,
            "triggered_rules": self.triggered_rules,
            "price_reference": self.price_reference,
            "stop_reference": self.stop_reference,
            "target_reference": self.target_reference,
        }


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return frame[name].astype(float)
    try:
        return pd.Series(float(name), index=frame.index, dtype=float)
    except ValueError as exc:  # pragma: no cover - guarded by validator
        raise KeyError(f"unknown column '{name}'") from exc


def _compare(op: str, left: pd.Series, right: pd.Series) -> pd.Series:
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "crosses_above":
        prev_left = left.shift(1)
        prev_right = right.shift(1)
        return (left > right) & (prev_left <= prev_right)
    if op == "crosses_below":
        prev_left = left.shift(1)
        prev_right = right.shift(1)
        return (left < right) & (prev_left >= prev_right)
    raise ValueError(f"unsupported operator '{op}'")


def _false_series(index: pd.Index) -> pd.Series:
    return pd.Series(False, index=index, dtype=bool)


def _eval_state(
    node: Condition | ConditionGroup, frame: pd.DataFrame, triggered: dict[str, set[str]]
) -> tuple[pd.Series, pd.Series]:
    """Return ``(satisfied, partial)`` masks for one condition node.

    ``partial`` is only ever True for an ``all`` group where some — but not all —
    children hold, which is exactly the documented ``WAIT`` situation: there is a
    candidate to watch, but the entry is not confirmed yet.
    """

    index = frame.index
    if isinstance(node, Condition):
        result = _compare(node.op, _series(frame, node.left), _series(frame, node.right))
        result = result.fillna(False).astype(bool)
        hit = set(frame.index[result])
        if hit:
            key = f"{node.op}:{node.left}:{node.right}"
            triggered.setdefault(key, set()).update(hit)
        return result, _false_series(index)

    if node.all is not None:
        children = list(node.all)
        if not children:
            return pd.Series(True, index=index), _false_series(index)
        satisfied, _ = _eval_state(children[0], frame, triggered)
        any_satisfied = satisfied.copy()
        all_satisfied = satisfied.copy()
        for child in children[1:]:
            child_sat, _ = _eval_state(child, frame, triggered)
            all_satisfied &= child_sat
            any_satisfied |= child_sat
        return all_satisfied, (any_satisfied & ~all_satisfied)

    satisfied = _false_series(index)
    for child in node.any or []:
        child_sat, _ = _eval_state(child, frame, triggered)
        satisfied |= child_sat
    return satisfied, _false_series(index)


def evaluate_group(
    group: ConditionGroup | None, frame: pd.DataFrame
) -> tuple[pd.Series, dict[str, set[pd.Timestamp]]]:
    """Evaluate a condition group, returning the mask and per-rule hit index."""

    if group is None:
        return pd.Series(False, index=frame.index, dtype=bool), {}
    triggered: dict[str, set[pd.Timestamp]] = {}
    mask, _partial = _eval_state(group, frame, triggered)
    return mask, triggered


def run_strategy(
    spec: StrategySpec,
    frame: pd.DataFrame,
    *,
    price_col: str = "close",
    atr_col: str = "atr14",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Evaluate entry/exit rules over ``frame``.

    Returns a per-bar frame with ``entry_long``/``exit_long`` flags and a summary
    dict containing the last non-empty signal intent.
    """

    out = pd.DataFrame(index=frame.index)
    triggered: dict[str, set[pd.Timestamp]] = {}

    entry_long, entry_long_partial = _eval_group_pair(spec.entry.long, frame, triggered)
    exit_long, _ = _eval_group_pair(spec.exit.long, frame, triggered)

    out["entry_long"] = entry_long
    out["entry_long_partial"] = entry_long_partial
    out["exit_long"] = exit_long
    out["risk_stop"] = _stop_reference(spec, frame, price_col, atr_col)
    out["risk_target"] = _target_reference(spec, frame, price_col, atr_col)

    if spec.entry.short is not None:
        entry_short, entry_short_partial = _eval_group_pair(spec.entry.short, frame, triggered)
        exit_short, _ = _eval_group_pair(spec.exit.short, frame, triggered)
        out["entry_short"] = entry_short
        out["entry_short_partial"] = entry_short_partial
        out["exit_short"] = exit_short
        # Short positions mirror the risk levels: stop above price, target below.
        out["risk_stop_short"] = _stop_reference(spec, frame, price_col, atr_col, is_long=False)
        out["risk_target_short"] = _target_reference(spec, frame, price_col, atr_col, is_long=False)

    out["close"] = frame[price_col]

    last_intent = _last_intent(out, triggered)
    return out, last_intent


def _eval_group_pair(
    group: ConditionGroup | None, frame: pd.DataFrame, triggered: dict[str, set[pd.Timestamp]]
) -> tuple[pd.Series, pd.Series]:
    if group is None:
        return _false_series(frame.index), _false_series(frame.index)
    return _eval_state(group, frame, triggered)


def _stop_reference(
    spec: StrategySpec,
    frame: pd.DataFrame,
    price_col: str,
    atr_col: str,
    *,
    is_long: bool = True,
) -> pd.Series:
    risk = spec.risk
    if risk is None or risk.stop_loss_atr_multiple is None or atr_col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    offset = risk.stop_loss_atr_multiple * frame[atr_col]
    return frame[price_col] - offset if is_long else frame[price_col] + offset


def _target_reference(
    spec: StrategySpec,
    frame: pd.DataFrame,
    price_col: str,
    atr_col: str,
    *,
    is_long: bool = True,
) -> pd.Series:
    risk = spec.risk
    if risk is None:
        return pd.Series(np.nan, index=frame.index)
    close = frame[price_col]
    sign = 1.0 if is_long else -1.0
    if risk.take_profit_atr_multiple is not None and atr_col in frame.columns:
        return close + sign * risk.take_profit_atr_multiple * frame[atr_col]
    if risk.take_profit_r_multiple is not None:
        stop = _stop_reference(spec, frame, price_col, atr_col, is_long=is_long)
        distance = (close - stop) * sign
        return close + sign * risk.take_profit_r_multiple * distance
    return pd.Series(np.nan, index=frame.index)


def _last_intent(out: pd.DataFrame, triggered: dict[str, set[pd.Timestamp]]) -> dict[str, Any]:
    """Signal intent of the **latest** bar (the bar a scan is run against).

    A historical match is no longer projected onto the latest bar: if nothing
    fires on the last closed bar, the state is NO_SIGNAL (or WAIT when the entry
    conditions are only partially met).
    """

    if out.empty:
        return {"state": "NO_SIGNAL", "direction": "FLAT", "bar_time": None}

    ts = out.index[-1]

    def flag(name: str) -> bool:
        return bool(out.at[ts, name]) if name in out.columns else False

    entry = flag("entry_long")
    entry_short = flag("entry_short")
    exit_long = flag("exit_long")
    exit_short = flag("exit_short")
    partial = flag("entry_long_partial") or flag("entry_short_partial")

    if entry:
        state, direction = "BUY", "LONG"
    elif entry_short:
        state, direction = "SELL", "SHORT"
    elif exit_long or exit_short:
        state, direction = "SELL", "FLAT"
    elif partial:
        fired = [rule for rule, hits in triggered.items() if ts in hits]
        return SignalIntent(
            state="WAIT",
            direction="FLAT",
            bar_time=ts,
            triggered_rules=sorted(fired),
            price_reference=_as_float(out.at[ts, "close"]),
            stop_reference=_as_float(out.at[ts, "risk_stop"]),
            target_reference=_as_float(out.at[ts, "risk_target"]),
        ).as_dict()
    else:
        return {"state": "NO_SIGNAL", "direction": "FLAT", "bar_time": None}

    fired = [rule for rule, hits in triggered.items() if ts in hits]
    return SignalIntent(
        state=state,
        direction=direction,
        bar_time=ts,
        triggered_rules=sorted(fired),
        price_reference=_as_float(out.at[ts, "close"]),
        stop_reference=_as_float(out.at[ts, "risk_stop"]),
        target_reference=_as_float(out.at[ts, "risk_target"]),
    ).as_dict()


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(result):
        return None
    return result
