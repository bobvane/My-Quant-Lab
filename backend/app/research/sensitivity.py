"""Parameter sensitivity analysis (docs/21, ADR-040).

Sweeps a strategy's declared parameters over a grid and reports how the
deterministic engine's metrics respond. This is a *descriptive* tool:

* it never picks "the best" parameters as a recommendation — the report ranks
  points so a human can see the shape of the surface, and every number comes from
  :func:`app.research.engine.run_backtest`;
* it never lets the AI invent or adjust a metric (docs/02 §3);
* grid points are evaluated independently and deterministically, so the same spec +
  dataset + grid always yields the same report.

The whole feature depends on ``run_backtest`` actually applying parameter
overrides; before ADR-040 it silently ignored them, which made every grid point
identical.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import pandas as pd

from app.research.engine import resolve_parameters, run_backtest
from app.strategies.dsl import StrategySpec, merge_spec_overrides

__all__ = [
    "SENSITIVITY_VERSION",
    "MAX_GRID_POINTS",
    "EXECUTION_AXES",
    "TRACKED_METRICS",
    "expand_grid",
    "run_sensitivity",
]

SENSITIVITY_VERSION = "1.0.0"

# Grid keys that address `execution.sizing` instead of a strategy parameter. Kept
# deliberately tiny: each one is a documented, validated override.
EXECUTION_AXES: frozenset[str] = frozenset({"risk_pct"})

# A sweep multiplies the cost of one backtest by the number of grid points. Cap it
# so a typo cannot pin a NAS CPU for an hour; the API surfaces this as a 422.
MAX_GRID_POINTS = 144

# Metrics carried through for every grid point, in report order. Kept small and
# explicit: these are the numbers the surface is drawn from.
TRACKED_METRICS: tuple[str, ...] = (
    "total_return",
    "cagr",
    "sharpe",
    "sortino",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "expectancy",
    "number_of_trades",
    "exposure",
)


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of ``grid`` as a list of override dicts.

    The axis order is the insertion order of ``grid`` and values keep their given
    order, so the returned list is deterministic — the same request always walks
    the grid in the same sequence.
    """

    if not grid:
        return []
    keys = list(grid.keys())
    for key in keys:
        values = grid[key]
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError(f"grid axis '{key}' must be a non-empty list")

    combinations = itertools.product(*(grid[key] for key in keys))
    return [dict(zip(keys, combo, strict=True)) for combo in combinations]


def run_sensitivity(
    spec: StrategySpec,
    bars: pd.DataFrame,
    *,
    grid: dict[str, list[Any]],
    base_parameters: dict[str, Any] | None = None,
    metric: str = "sharpe",
    strategy_version: str = "unversioned",
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Evaluate every grid point and aggregate the resulting metrics.

    ``metric`` selects the objective used for ranking and for the stability
    statistics. Grid points whose objective is undefined (too few samples, or no
    trades) stay in ``points`` with a ``None`` objective but are excluded from the
    rankings and the spread statistics — the report says "unknown" rather than
    inventing a zero.
    """

    if metric not in TRACKED_METRICS:
        raise ValueError(
            f"unsupported metric '{metric}'; choose one of {', '.join(TRACKED_METRICS)}"
        )

    # A grid key is either a strategy parameter (resolved by period_ref) or one of the
    # few execution axes this sweep understands. `risk_pct` lives in `execution.sizing`
    # rather than `parameters`, but "does this strategy survive a different risk
    # budget?" is exactly the kind of neighbourhood question this report exists to
    # answer (docs/23 §7).
    effective_base, _ = resolve_parameters(spec, base_parameters)
    execution_axes = [key for key in grid if key in EXECUTION_AXES]
    if len(execution_axes) > 1:
        raise ValueError(
            "only one execution axis per sweep, got: " + ", ".join(sorted(execution_axes))
        )
    param_axes = [key for key in grid if key not in EXECUTION_AXES]
    unknown_axes = sorted(key for key in param_axes if key not in effective_base)
    if unknown_axes:
        raise ValueError(
            "grid axis/axes not declared by the strategy: "
            + ", ".join(unknown_axes)
            + f"; this strategy declares: {', '.join(sorted(effective_base)) or '(none)'}"
            + f"; execution axes available: {', '.join(sorted(EXECUTION_AXES))}"
        )

    overrides = expand_grid(grid)
    if not overrides:
        raise ValueError("grid is empty; provide at least one axis with one value")
    if len(overrides) > MAX_GRID_POINTS:
        raise ValueError(
            f"grid has {len(overrides)} points, the maximum is {MAX_GRID_POINTS}; narrow the axes"
        )

    points: list[dict[str, Any]] = []
    for combo in overrides:
        exec_combo = {k: v for k, v in combo.items() if k in EXECUTION_AXES}
        params = {**effective_base, **{k: v for k, v in combo.items() if k not in EXECUTION_AXES}}
        point_spec = spec
        if exec_combo:
            # risk_per_trade is the only mode where risk_pct has any effect, so a sweep
            # of risk_pct implies that mode unless the strategy already asks for it.
            if spec.execution.sizing.mode != "risk_per_trade":
                point_spec = merge_spec_overrides(
                    spec,
                    {"execution": {"sizing": {"mode": "risk_per_trade", **exec_combo}}},
                )
            else:
                point_spec = merge_spec_overrides(spec, {"execution": {"sizing": exec_combo}})
        result = run_backtest(
            point_spec,
            bars,
            strategy_version=strategy_version,
            timeframe=timeframe,
            parameters=params,
        )
        metrics = result.metrics
        points.append(
            {
                "parameters": _jsonable(combo),
                "metrics": {name: metrics.get(name) for name in TRACKED_METRICS},
                "objective": metrics.get(metric),
                "result_hash": result.result_hash,
                "warnings": list(result.warnings),
            }
        )

    defined = [p for p in points if p["objective"] is not None]
    values = [float(p["objective"]) for p in defined]

    ranked = sorted(defined, key=lambda p: float(p["objective"]), reverse=True)
    best = ranked[0] if ranked else None
    worst = ranked[-1] if ranked else None

    return {
        "sensitivity_version": SENSITIVITY_VERSION,
        "metric": metric,
        "axes": {key: _jsonable(list(values)) for key, values in grid.items()},
        "grid_points": len(points),
        "evaluated_points": len(defined),
        "points": points,
        "summary": {
            "mean": _mean(values),
            "median": _median(values),
            "stdev": _stdev(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "range": (max(values) - min(values)) if values else None,
            "positive_ratio": (sum(1 for v in values if v > 0) / len(values) if values else None),
        },
        "best": _compact(best),
        "worst": _compact(worst),
        # The spread of the objective across the grid is the actual research
        # question: a strategy that only works at one exact setting is fragile, and
        # the report should say so rather than hide it behind the best point.
        "stable": _is_stable(values),
    }


def _compact(point: dict[str, Any] | None) -> dict[str, Any] | None:
    if point is None:
        return None
    return {
        "parameters": point["parameters"],
        "objective": point["objective"],
        "result_hash": point["result_hash"],
    }


def _is_stable(values: list[float]) -> bool | None:
    """True when every evaluated grid point keeps the same sign.

    Sign-consistency is deliberately crude but honest: it answers "does this
    strategy make/lose money across the whole neighbourhood, or does the sign flip
    with the parameters?" without inventing a statistical significance test the
    sample size cannot support.
    """

    if not values:
        return None
    return all(v > 0 for v in values) or all(v < 0 for v in values)


def _jsonable(value: Any) -> Any:
    """Coerce numpy scalars / Timestamps so the payload is JSON-serialisable."""

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool | int | str) or value is None:
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item())
        except (TypeError, ValueError):
            pass
    return value


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)
