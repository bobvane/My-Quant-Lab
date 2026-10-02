"""Monte Carlo resampling of a completed backtest (docs/22, ADR-043).

One backtest is a *single* historical path. It answers "what happened", not "what
could plausibly happen". This module re-samples the strategy's own realised trades
to build a distribution, so you can see the spread around the reported result:
a 12% return from a strategy whose resampled paths span -20%..+60% is a very
different proposition from the same 12% with paths spanning +8%..+16%.

Method (trade-level bootstrap, IID):

1. Take the trade P&L list of one real backtest and a fixed initial capital.
2. Draw ``trades_per_run`` trades **with replacement** from that list.
3. Compound them onto the equity curve (``equity *= (1 + pnl / equity)``) so each
   simulated trade risks the same *fraction* of equity as the original did.
4. Record final return, max drawdown, and a Sharpe/Sortino from the path.
5. Report percentiles, the probability of a profit, and tail risk across runs.

Assumptions that matter, stated so the report cannot overstate itself:

* Trades are treated as **independent and identically distributed**. Real trades
  autocorrelate (regimes, volatility clustering), so this understates the chance of
  long losing streaks. It is a resampling of history, not a forecast.
* The *order* of trades is resampled, which is exactly what makes drawdown
  dispersion visible — the same trades in a different order can draw down far more.
* Only the strategy's own realised trades are used. A strategy that traded twice
  produces a distribution built from two observations, and the report says so via
  ``warnings`` rather than pretending to precision it does not have.

Everything here is deterministic given ``seed`` and comes from the deterministic
engine; the AI layer never produces or adjusts these numbers (docs/02 §3).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.research.metrics import BARRS_PER_YEAR

__all__ = ["MONTE_CARLO_VERSION", "MAX_RUNS", "DEFAULT_RUNS", "run_monte_carlo"]

MONTE_CARLO_VERSION = "1.0.0"

# Each run is cheap, but the request is synchronous on a NAS: cap it so a typo
# cannot occupy a worker for minutes.
MAX_RUNS = 5_000
DEFAULT_RUNS = 1_000

# Below this many observed trades the resampled distribution is not informative;
# the report still runs but carries an explicit warning.
MIN_TRADES_FOR_CONFIDENCE = 20

_PERCENTILES: tuple[float, ...] = (5.0, 25.0, 50.0, 75.0, 95.0)


def _percentiles(values: np.ndarray) -> dict[str, float | None]:
    if values.size == 0:
        return {f"p{int(p)}": None for p in _PERCENTILES}
    result = np.percentile(values, _PERCENTILES)
    return {f"p{int(p)}": float(v) for p, v in zip(_PERCENTILES, result, strict=True)}


def _max_drawdown(equity: np.ndarray) -> float:
    """Worst peak-to-trough decline of an equity path (negative or 0)."""

    if equity.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = np.where(running_max > 0, equity / running_max - 1.0, 0.0)
    return float(np.min(drawdown))


def _sharpe(equity: np.ndarray, periods: float) -> float | None:
    if equity.size < 3:
        return None
    returns = np.diff(equity) / equity[:-1]
    std = float(np.std(returns, ddof=1))
    if std <= 0:
        return None
    return float(np.mean(returns) / std * math.sqrt(periods))


def _sortino(equity: np.ndarray, periods: float) -> float | None:
    if equity.size < 3:
        return None
    returns = np.diff(equity) / equity[:-1]
    downside = returns[returns < 0]
    if downside.size < 2:
        return None
    downside_std = float(np.std(downside, ddof=1))
    if downside_std <= 0:
        return None
    return float(np.mean(returns) / downside_std * math.sqrt(periods))


def run_monte_carlo(
    trades: list[dict[str, Any]],
    *,
    initial_capital: float,
    runs: int = DEFAULT_RUNS,
    trades_per_run: int | None = None,
    seed: int = 0,
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Bootstrap-resample a backtest's trades into a distribution of outcomes.

    ``trades`` is the ``trades`` list of a :class:`BacktestResult`. Only ``pnl`` is
    used, because the simulation compounds P&L relative to current equity rather
    than replaying exact position sizes.
    """

    if runs < 1:
        raise ValueError("runs must be >= 1")
    if runs > MAX_RUNS:
        raise ValueError(f"runs={runs} exceeds the maximum of {MAX_RUNS}")

    pnl_values = [
        float(t["pnl"])
        for t in trades
        if t.get("pnl") is not None and math.isfinite(float(t["pnl"]))
    ]
    if not pnl_values:
        raise ValueError("the backtest has no closed trades to resample")

    capital = float(initial_capital)
    if capital <= 0:
        raise ValueError("initial_capital must be > 0")

    n_observed = len(pnl_values)
    # `if trades_per_run` would silently swallow an explicit 0 (falsy) and quietly
    # fall back to the observed count; check for None explicitly.
    if trades_per_run is None:
        n_draw = n_observed
    else:
        n_draw = int(trades_per_run)
        if n_draw < 1:
            raise ValueError("trades_per_run must be >= 1")

    warnings: list[str] = []
    if n_observed < MIN_TRADES_FOR_CONFIDENCE:
        warnings.append(
            f"only {n_observed} observed trade(s): the resampled distribution is "
            "built from a very small sample"
        )

    # One RNG for the whole request, seeded -> the same request always yields the
    # same distribution.
    rng = np.random.default_rng(seed)
    pool = np.asarray(pnl_values, dtype=float)

    final_equity = np.empty(runs, dtype=float)
    total_return = np.empty(runs, dtype=float)
    max_drawdown = np.empty(runs, dtype=float)
    sharpe = np.full(runs, np.nan, dtype=float)
    sortino = np.full(runs, np.nan, dtype=float)

    periods = BARRS_PER_YEAR.get(timeframe, 252.0)
    equity = np.empty(n_draw + 1, dtype=float)
    # Keep a bounded set of paths for a fan chart; storing all of them would bloat
    # the response for no visual benefit.
    sample_runs = min(runs, 100)
    sample_stride = max(1, runs // sample_runs)
    sample_paths: list[list[float]] = []

    for i in range(runs):
        draws = rng.choice(pool, size=n_draw, replace=True)
        equity[0] = capital
        # Compound: a trade's P&L is treated as its return on the *original*
        # capital and applied to the running equity. So a +100 trade on 10,000
        # capital is a +1% move, and resampled paths compound those moves
        # multiplicatively in the drawn order. (Computing pnl/prev instead would
        # mis-scale the P&L of a later trade, since it was earned against the
        # capital of its own time, not against the simulated equity.)
        equity[1:] = capital * np.cumprod(1.0 + draws / capital)
        if not np.all(np.isfinite(equity)):
            # A path that wiped out cannot continue; clamp rather than emit NaN.
            equity = np.nan_to_num(equity, nan=0.0, posinf=0.0, neginf=0.0)

        final = float(equity[-1])
        final_equity[i] = final
        total_return[i] = final / capital - 1.0
        max_drawdown[i] = _max_drawdown(equity)
        s = _sharpe(equity, periods)
        if s is not None:
            sharpe[i] = s
        so = _sortino(equity, periods)
        if so is not None:
            sortino[i] = so

        if sample_paths and len(sample_paths) < sample_runs and i % sample_stride == 0:
            sample_paths.append([float(v) for v in equity])

    if not sample_paths:
        sample_paths.append([float(v) for v in equity])

    sharpe_defined = sharpe[np.isfinite(sharpe)]
    sortino_defined = sortino[np.isfinite(sortino)]

    summary = {
        "runs": runs,
        "observed_trades": n_observed,
        "trades_per_run": n_draw,
        "initial_capital": capital,
        "final_equity": _percentiles(final_equity),
        "total_return": _percentiles(total_return),
        "max_drawdown": _percentiles(max_drawdown),
        "sharpe": _percentiles(sharpe_defined),
        "sortino": _percentiles(sortino_defined),
        # Headline probabilities. These are frequencies in the simulation, not
        # forecasts of the future.
        "probability_of_profit": float(np.mean(total_return > 0)),
        "probability_of_loss": float(np.mean(total_return < 0)),
        "probability_of_ruin": float(np.mean(final_equity <= 0.0)),
        "expected_total_return": float(np.mean(total_return)),
        "expected_max_drawdown": float(np.mean(max_drawdown)),
        "worst_max_drawdown": float(np.min(max_drawdown)),
    }

    return {
        "monte_carlo_version": MONTE_CARLO_VERSION,
        "seed": seed,
        "timeframe": timeframe,
        "method": "trade_level_iid_bootstrap",
        "summary": summary,
        "sample_equity_paths": sample_paths,
        "warnings": warnings,
    }
