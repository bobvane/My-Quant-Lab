"""Performance metrics computed deterministically by the engine.

All metrics are produced here, never by an LLM. When the sample size is too
small a metric is reported as ``None`` and rendered as ``N/A`` in the UI instead
of being faked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["Metrics", "compute_metrics", "BARRS_PER_YEAR"]

BARRS_PER_YEAR: dict[str, float] = {
    "1m": 98_280.0,
    "5m": 19_656.0,
    "15m": 6_552.0,
    "1h": 1_638.0,
    "4h": 409.5,
    "1d": 252.0,
    "1w": 52.0,
}

MIN_SAMPLES_SHARPE = 2
MIN_SAMPLES_WINRATE = 1


@dataclass
class Metrics:
    initial_capital: float
    final_equity: float
    total_return: float | None = None
    cagr: float | None = None
    annualized_volatility: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    max_drawdown_duration_bars: int | None = None
    number_of_trades: int = 0
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    average_holding_bars: float | None = None
    exposure: float | None = None
    turnover: float | None = None
    max_consecutive_losses: int | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in self.__dict__.items() if k != "notes"}
        return payload


def _safe(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def compute_metrics(
    equity_curve: np.ndarray,
    trades: list[dict[str, Any]],
    *,
    timeframe: str = "1d",
    risk_free_rate: float = 0.0,
    bars_per_year: float | None = None,
) -> Metrics:
    """Compute the V1 metric set from an equity curve and the trade list."""

    equity = np.asarray(equity_curve, dtype=float)
    notes: list[str] = []
    initial = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0
    metrics = Metrics(initial_capital=initial, final_equity=final, notes=notes)

    if len(equity) == 0:
        notes.append("equity curve too short for ratio metrics")
        return metrics

    if initial <= 0:
        # A return needs a positive denominator. A paper account withdrawn down to (or
        # past) its deposits, and a backtest with no starting capital, both land here:
        # report the equity that exists and withhold the ratios rather than divide by it
        # (ADR-066). This is checked before the curve length: an account withdrawn to
        # zero has a one-point curve, and "too short" would answer the wrong question —
        # the ratios are missing because there is nothing to divide by, not because the
        # account is young.
        notes.append("initial capital is not positive, so ratio metrics have no denominator")
        return metrics

    if len(equity) < 2:
        notes.append("equity curve too short for ratio metrics")
        return metrics

    metrics.total_return = final / initial - 1.0

    returns = np.diff(equity) / equity[:-1]
    periods = bars_per_year or BARRS_PER_YEAR.get(timeframe, 252.0)
    years = len(equity) / periods
    if years > 0:
        metrics.cagr = (final / initial) ** (1.0 / years) - 1.0
    else:
        notes.append("period too short for CAGR")

    if len(returns) >= 2:
        std = float(np.std(returns, ddof=1))
        metrics.annualized_volatility = std * math.sqrt(periods)
        if std > 0 and len(returns) >= MIN_SAMPLES_SHARPE:
            mean_return = float(np.mean(returns))
            periods_rf = risk_free_rate / periods
            metrics.sharpe = (mean_return - periods_rf) / std * math.sqrt(periods)
        else:
            notes.append("not enough return samples for Sharpe")

        downside = returns[returns < 0]
        if len(downside) >= MIN_SAMPLES_SHARPE:
            downside_std = float(np.std(downside, ddof=1))
            if downside_std > 0:
                metrics.sortino = (
                    (float(np.mean(returns)) - risk_free_rate / periods)
                    / (downside_std)
                    * math.sqrt(periods)
                )
        else:
            notes.append("not enough downside samples for Sortino")

    running_max = np.maximum.accumulate(equity)
    drawdown = np.where(running_max > 0, equity / running_max - 1.0, 0.0)
    metrics.max_drawdown = float(drawdown.min())
    metrics.max_drawdown_duration_bars = _max_drawdown_duration(drawdown)

    metrics.number_of_trades = len(trades)
    pnl_values = [float(t["pnl"]) for t in trades if t.get("pnl") is not None]
    if len(pnl_values) >= MIN_SAMPLES_WINRATE:
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        metrics.win_rate = len(wins) / len(pnl_values)
        metrics.avg_win = float(np.mean(wins)) if wins else 0.0
        metrics.avg_loss = float(np.mean(losses)) if losses else 0.0
        gross_profit = float(sum(wins))
        gross_loss = abs(float(sum(losses)))
        if gross_loss > 0:
            metrics.profit_factor = gross_profit / gross_loss
        else:
            metrics.profit_factor = None
            notes.append("no losing trades: profit factor is undefined")
        metrics.expectancy = float(np.mean(pnl_values))
        metrics.max_consecutive_losses = _max_consecutive_losses(pnl_values)
    else:
        notes.append("no closed trades: trade metrics are N/A")

    holding = [t.get("holding_bars") for t in trades if t.get("holding_bars") is not None]
    if holding:
        metrics.average_holding_bars = float(np.mean(holding))
    return metrics


def _max_drawdown_duration(drawdown: np.ndarray) -> int:
    longest = 0
    current = 0
    for value in drawdown:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _max_consecutive_losses(pnl_values: list[float]) -> int:
    longest = 0
    current = 0
    for value in pnl_values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
