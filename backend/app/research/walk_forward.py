"""Walk-forward / out-of-sample research helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.research.engine import run_backtest
from app.strategies.dsl import StrategySpec

__all__ = ["WalkForwardWindow", "run_holdout", "run_walk_forward"]


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def _split_bounds(
    index: pd.DatetimeIndex, train_bars: int, test_bars: int, step: int | None
) -> list[WalkForwardWindow]:
    step = step or test_bars
    windows: list[WalkForwardWindow] = []
    cursor = 0
    while cursor + train_bars + test_bars <= len(index):
        train_start = cursor
        train_end = cursor + train_bars - 1
        test_start = train_end + 1
        test_end = test_start + test_bars - 1
        windows.append(
            WalkForwardWindow(
                index=len(windows) + 1,
                train_start=index[train_start],
                train_end=index[train_end],
                test_start=index[test_start],
                test_end=index[test_end],
            )
        )
        cursor += step
    return windows


def run_walk_forward(
    spec: StrategySpec,
    bars: pd.DataFrame,
    *,
    train_bars: int,
    test_bars: int,
    step: int | None = None,
    strategy_version: str = "unversioned",
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Rolling train -> test evaluation.

    Parameters are never optimised against the test window: the same spec is used
    for every window so the report shows *out-of-sample* behaviour, not curve
    fitting.
    """

    frame = bars.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame["timestamp"])
    frame = frame.sort_index()
    index = frame.index

    windows = _split_bounds(index, train_bars, test_bars, step)
    segments: list[dict[str, Any]] = []

    for window in windows:
        train_slice = frame.loc[window.train_start : window.train_end]
        test_slice = frame.loc[window.test_start : window.test_end]
        in_sample = run_backtest(
            spec, train_slice, strategy_version=strategy_version, timeframe=timeframe
        )
        out_sample = run_backtest(
            spec, test_slice, strategy_version=strategy_version, timeframe=timeframe
        )
        segments.append(
            {
                "window": window.index,
                "train_start": window.train_start.isoformat(),
                "train_end": window.train_end.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "in_sample": _summarise(in_sample),
                "out_of_sample": _summarise(out_sample),
            }
        )

    oos_returns = [
        s["out_of_sample"]["total_return"]
        for s in segments
        if s["out_of_sample"]["total_return"] is not None
    ]
    is_returns = [
        s["in_sample"]["total_return"]
        for s in segments
        if s["in_sample"]["total_return"] is not None
    ]
    return {
        "windows": len(segments),
        "train_bars": train_bars,
        "test_bars": test_bars,
        "segments": segments,
        "summary": {
            "mean_is_return": _mean(is_returns),
            "mean_oos_return": _mean(oos_returns),
            "positive_oos_windows": sum(1 for r in oos_returns if r > 0),
            "consistency": (
                sum(1 for r in oos_returns if r > 0) / len(oos_returns) if oos_returns else None
            ),
        },
    }


def run_holdout(
    spec: StrategySpec,
    bars: pd.DataFrame,
    *,
    oos_pct: float | None = None,
    oos_start: str | None = None,
    strategy_version: str = "unversioned",
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Single train/test split for out-of-sample validation (docs/07 §11).

    The test window is either the last ``oos_pct`` of the bars (default 20%) or
    everything from ``oos_start`` onwards. The same spec is used for both sides;
    parameters are never fitted on the test window.
    """

    frame = bars.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame["timestamp"])
    frame = frame.sort_index()
    total = len(frame)
    if total < 2:
        raise ValueError("need at least 2 bars for an out-of-sample split")

    if oos_start:
        split_ts = pd.Timestamp(oos_start)
        if split_ts.tzinfo is None and frame.index.tz is not None:
            split_ts = split_ts.tz_localize(frame.index.tz)
        train = frame.loc[frame.index < split_ts]
        test = frame.loc[frame.index >= split_ts]
    else:
        pct = 0.2 if oos_pct is None else float(oos_pct)
        if not 0.0 < pct < 1.0:
            raise ValueError("oos_pct must be between 0 and 1 (exclusive)")
        cut = max(1, int(round(total * (1.0 - pct))))
        train = frame.iloc[:cut]
        test = frame.iloc[cut:]

    if len(train) == 0 or len(test) == 0:
        raise ValueError("the out-of-sample split leaves an empty train or test window")

    in_sample = run_backtest(spec, train, strategy_version=strategy_version, timeframe=timeframe)
    out_sample = run_backtest(spec, test, strategy_version=strategy_version, timeframe=timeframe)
    return {
        "split_time": test.index[0].isoformat(),
        "in_sample_bars": len(train),
        "out_of_sample_bars": len(test),
        "in_sample": _summarise(in_sample),
        "out_of_sample": _summarise(out_sample),
    }


def _summarise(result: Any) -> dict[str, Any]:
    metrics = result.metrics
    return {
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe": metrics.get("sharpe"),
        "win_rate": metrics.get("win_rate"),
        "number_of_trades": metrics.get("number_of_trades"),
        "result_hash": result.result_hash,
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
