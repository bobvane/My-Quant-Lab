"""Phase C: performance, risk and buy-and-hold comparison for a stored run.

Everything here is derived from what the backtest engine already persisted -- the
per-bar equity curve (``backtest_results.equity_curve_json``, which carries the
close of every bar) and the stored metric block -- so the analysis needs no new
table, no market-data read on the common path, and no re-run. Nothing is written
back: this is a view over an immutable result (ADR-187, ADR-188).

Two rules shape the module:

* :func:`app.research.metrics.compute_metrics` stays the only place where a ratio
  is produced from an equity curve. The buy-and-hold comparison is fed *through*
  it rather than growing a second implementation that could drift;
* every number leaving here is finite or ``None``. ``None`` means "this data does
  not support that number" -- never a fake 0 -- and the ``caveats`` list carries
  the machine-readable reason a human can read.

The bar counts that decide the sample tier are the two gates the project already
uses for evidence (``MIN_TRADES_PRELIMINARY`` mirrors ``LifecycleThresholds``
``min_backtest_trades``, ``MIN_TRADES_ENOUGH`` is the Monte-Carlo confidence
gate). They are not new thresholds; a test pins the mirror so it cannot drift.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from app.research.metrics import BARRS_PER_YEAR, compute_metrics
from app.research.monte_carlo import MIN_TRADES_FOR_CONFIDENCE

__all__ = [
    "ANALYSIS_VERSION",
    "BENCHMARK_KINDS",
    "DERIVED_METRICS",
    "analyse_run",
    "curve_has_closes",
    "curve_window",
]

ANALYSIS_VERSION = "1.0.0"

#: What this module adds on top of ``app.research.metrics``. The engine's own
#: metrics are listed by the ``metrics`` capability group; these names are the
#: derived ones, and ``app.capabilities`` imports this tuple instead of copying
#: it, so the registry an AI role reads cannot claim more than the analysis
#: actually returns.
DERIVED_METRICS: tuple[str, ...] = (
    "calmar",
    "downside_deviation",
    "excess_return",
    "final_equity_gap",
    "max_drawdown_duration_days",
    "recovery_period",
    "sample_tier",
    "worst_bar_return",
    "worst_month_return",
    "worst_trade",
)

#: Comparison series this module can derive from stored evidence.
BENCHMARK_KINDS: tuple[str, ...] = ("buy_and_hold",)

# Mirrors ``LifecycleThresholds.min_backtest_trades`` (app/strategies/lifecycle.py:96).
# Copied instead of imported on purpose: this module is pure (no session, no
# SQLAlchemy), and backend/tests/test_phase_c_analysis.py pins the two together.
MIN_TRADES_PRELIMINARY = 10
# The Monte-Carlo confidence gate, reused rather than re-invented.
MIN_TRADES_ENOUGH = MIN_TRADES_FOR_CONFIDENCE

TIER_INSUFFICIENT = "insufficient"
TIER_PRELIMINARY = "preliminary"
TIER_ENOUGH = "enough"

_TIER_TEXT = {
    TIER_INSUFFICIENT: "交易样本太少，这些比率只能当作线索。",
    TIER_PRELIMINARY: "交易样本偏少，结论仅供参考。",
    TIER_ENOUGH: "交易样本量足以支撑这些比率。",
}

# Bar length in minutes, used only to put the longest drawdown into human words
# ("about 3 months"). A multiplication, never a trading calendar: the project has
# no holiday calendar, so a day is a day.
_BAR_MINUTES: dict[str, float] = {
    "1m": 1.0,
    "5m": 5.0,
    "15m": 15.0,
    "1h": 60.0,
    "4h": 240.0,
    "1d": 1_440.0,
    "1w": 10_080.0,
}

_CAVEAT_TEXTS = {
    "no_equity_curve": "这次回测没有保存权益曲线，绩效与风险无法派生。",
    "curve_too_short": "权益曲线不足两个点，比率类指标无法计算。",
    "non_positive_initial_capital": "初始资金不是正数，比率类指标没有分母。",
    "non_finite_value": "数据里有无法在 JSON 中安全表示的数值，相关字段已置为未知。",
    "no_closed_trades": "这次回测没有已平仓交易，交易类指标不可用。",
    "benchmark_unavailable": "这次回测没有可用的收盘价序列，买入持有对照无法计算。",
    "benchmark_partial_window": "对照数据的 K 线少于策略，只对齐了能对上的区间。",
    "drawdown_not_recovered": "最深的一次回撤到回测结束还没有恢复。",
    "bars_per_year_252_for_crypto": "加密货币全年无休，年化仍按 252 个交易日折算。",
}


class _Caveats:
    """Ordered, de-duplicated caveats: first occurrence wins."""

    def __init__(self) -> None:
        self._items: list[dict[str, str]] = []
        self._seen: set[str] = set()

    def add(self, code: str) -> None:
        if code in self._seen:
            return
        self._seen.add(code)
        self._items.append({"code": code, "message": _CAVEAT_TEXTS.get(code, code)})

    def as_list(self) -> list[dict[str, str]]:
        return list(self._items)


def _finite(value: Any) -> float | None:
    """A JSON-safe float, or ``None``. NaN and both infinities are not numbers here."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_time(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    return None


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    running_max = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(running_max > 0, equity / running_max - 1.0, 0.0)


def _max_drawdown_duration(drawdown: np.ndarray) -> int:
    """Longest run of bars strictly below the previous peak (engine's definition)."""

    longest = 0
    current = 0
    for value in drawdown:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _recovery(equity: np.ndarray, drawdown: np.ndarray) -> tuple[int | None, bool | None]:
    """Bars from the deepest trough back to the peak it fell from.

    ``(None, False)`` means the curve never got back to that peak inside the
    window; ``(0, True)`` means there was no drawdown to recover from.
    """

    if len(equity) < 2:
        return None, None
    trough = int(np.argmin(drawdown))
    if float(drawdown[trough]) >= 0.0:
        return 0, True
    peak_index = int(np.argmax(equity[: trough + 1]))
    if peak_index == trough:  # the trough is the first bar: nothing to fall from
        return 0, True
    peak_value = float(equity[peak_index])
    for index in range(trough + 1, len(equity)):
        if float(equity[index]) >= peak_value:
            return index - trough, True
    return None, False


def _downside_deviation(returns: np.ndarray, periods: float) -> float | None:
    """Annualised deviation of the negative returns (the numerator Sortino lacks)."""

    if len(returns) < 2:
        return None
    downside = returns[returns < 0]
    if len(downside) == 0:
        return 0.0
    if len(downside) < 2:
        return None
    return float(np.std(downside, ddof=1)) * math.sqrt(periods)


def _calmar(cagr: float | None, max_drawdown: float | None) -> float | None:
    if cagr is None or max_drawdown is None or max_drawdown >= 0:
        return None
    return cagr / abs(max_drawdown)


def _worst_month(points: list[tuple[dt.datetime | None, float]]) -> float | None:
    """Worst calendar-month return, from the first to the last bar of each month."""

    if not points or any(moment is None for moment, _ in points):
        return None
    months: dict[tuple[int, int], list[float]] = {}
    for moment, value in points:
        assert moment is not None  # guarded above
        months.setdefault((moment.year, moment.month), []).append(value)
    changes = [
        values[-1] / values[0] - 1.0
        for values in months.values()
        # A month holding a single bar has no measurable change; including it would
        # report 0.0 as "the worst month", which is worse than saying nothing.
        if len(values) >= 2 and values[0] > 0
    ]
    return min(changes) if changes else None


def _worst_trade(trades: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [(float(t["pnl"]), t) for t in trades if _finite(t.get("pnl")) is not None]
    if not priced:
        return None
    pnl, trade = min(priced, key=lambda item: item[0])
    exit_time = trade.get("exit_time")
    return {
        "pnl": pnl,
        "exit_time": exit_time.isoformat() if hasattr(exit_time, "isoformat") else exit_time,
        "direction": trade.get("direction"),
    }


def _max_consecutive_losses(trades: Sequence[dict[str, Any]]) -> int | None:
    pnl_values = [float(t["pnl"]) for t in trades if _finite(t.get("pnl")) is not None]
    if not pnl_values:
        return None
    longest = 0
    current = 0
    for value in pnl_values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _sample_tier(trades: int) -> str:
    if trades < MIN_TRADES_PRELIMINARY:
        return TIER_INSUFFICIENT
    if trades < MIN_TRADES_ENOUGH:
        return TIER_PRELIMINARY
    return TIER_ENOUGH


def _close_column(points: Sequence[dict[str, Any]]) -> list[float] | None:
    """Every point's close, or ``None`` when any of them cannot carry a comparison."""

    closes: list[float] = []
    for point in points:
        close = _finite(point.get("close"))
        if close is None or close <= 0:
            return None
        closes.append(close)
    return closes or None


def _benchmark_curve_from_closes(closes: Sequence[float], initial_capital: float) -> list[float]:
    first = float(closes[0])
    return [initial_capital * float(close) / first for close in closes]


def curve_has_closes(equity_curve: Sequence[dict[str, Any]] | None) -> bool:
    """True when the stored curve can carry the comparison on its own.

    This is the cheap path: the engine writes the close of every bar into the
    curve, so a run stored by the current engine never needs a market-data read.
    """

    points = [point for point in (equity_curve or []) if isinstance(point, dict)]
    return bool(points) and _close_column(points) is not None


def curve_window(
    equity_curve: Sequence[dict[str, Any]] | None,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """First and last timestamp of the stored curve, so the fallback read can be narrowed."""

    moments = [
        _parse_time(point.get("timestamp"))
        for point in (equity_curve or [])
        if isinstance(point, dict)
    ]
    usable = [moment for moment in moments if moment is not None]
    if not usable:
        return None, None
    return usable[0], usable[-1]


def _benchmark_curve_from_bars(
    points: Sequence[tuple[dt.datetime | None, float]],
    bars: Sequence[dict[str, Any]],
    initial_capital: float,
) -> tuple[list[tuple[dt.datetime | None, float]], bool]:
    """Align stored bars to the strategy's own timestamps.

    Returns ``(curve, window_matched)`` where ``curve`` pairs each matched
    timestamp with the value the same money would have held. An empty curve means
    no timestamp could be matched; the caller turns that into ``benchmark: null``.
    """

    by_time: dict[str, float] = {}
    for bar in bars:
        moment = _parse_time(bar.get("timestamp"))
        close = _finite(bar.get("close"))
        if moment is None or close is None or close <= 0:
            continue
        by_time[moment.isoformat()] = close

    matched: list[tuple[dt.datetime | None, float]] = []
    for moment, _ in points:
        if moment is None:
            continue
        close = by_time.get(moment.isoformat())
        if close is not None:
            matched.append((moment, close))
    if not matched:
        return [], False
    values = _benchmark_curve_from_closes([close for _, close in matched], initial_capital)
    curve = [(moment, value) for (moment, _), value in zip(matched, values, strict=True)]
    return curve, len(curve) == len(points)


def _curve_points(
    curve: Sequence[tuple[dt.datetime | None, float]],
) -> list[dict[str, Any]]:
    """The comparison as a drawable series, so no client has to rebuild it.

    The values are the same money as the strategy's own curve (the run's initial
    capital at the first bar), which is what makes the two lines comparable
    without rescaling anything in the browser. Each point carries the timestamp of
    the bar it was computed from, so a client never has to assume alignment.
    """

    return [{"timestamp": _iso(moment), "equity": float(value)} for moment, value in curve]


def analyse_run(
    *,
    run_id: int | None = None,
    result_hash: str | None = None,
    metrics: dict[str, Any] | None = None,
    equity_curve: Sequence[dict[str, Any]] | None = None,
    trades: Sequence[dict[str, Any]] | None = None,
    timeframe: str = "1d",
    asset_class: str | None = None,
    fallback_bars: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive performance, risk and the buy-and-hold comparison for one stored run.

    ``fallback_bars`` is only read when the stored curve has no usable ``close``
    column -- a run stored before the curve carried closes, or a curve whose
    closes were dropped. It must be the bars of *this run's* series, restricted to
    the run's window by the caller.
    """

    caveats = _Caveats()
    stored = dict(metrics) if metrics else {}

    raw_points = [point for point in (equity_curve or []) if isinstance(point, dict)]
    if not raw_points:
        caveats.add("no_equity_curve")

    # A non-finite equity cannot be divided, averaged or JSON-encoded, so that bar
    # leaves the curve entirely and the caveat names what happened.
    points: list[tuple[dt.datetime | None, float]] = []
    for point in raw_points:
        value = _finite(point.get("equity"))
        if value is None:
            caveats.add("non_finite_value")
            continue
        points.append((_parse_time(point.get("timestamp")), value))

    initial_capital = _finite(stored.get("initial_capital"))
    if initial_capital is None and points:
        initial_capital = points[0][1]
    if initial_capital is not None and initial_capital <= 0:
        caveats.add("non_positive_initial_capital")

    usable = initial_capital is not None and initial_capital > 0 and len(points) >= 2
    if raw_points and not usable:
        if initial_capital is None or initial_capital <= 0:
            caveats.add("non_positive_initial_capital")
        else:
            caveats.add("curve_too_short")

    equity = np.asarray([value for _, value in points], dtype=float)
    periods = BARRS_PER_YEAR.get(timeframe, 252.0)

    max_drawdown = _finite(stored.get("max_drawdown"))
    max_drawdown_duration = stored.get("max_drawdown_duration_bars")
    recovery_bars: int | None = None
    recovered: bool | None = None
    worst_bar_return: float | None = None
    worst_month_return: float | None = None
    downside_deviation: float | None = None
    returns = np.asarray([], dtype=float)

    if usable:
        with np.errstate(divide="ignore", invalid="ignore"):
            raw_returns = np.diff(equity) / equity[:-1]
        returns = np.asarray(
            [float(value) for value in raw_returns if math.isfinite(float(value))], dtype=float
        )
        drawdown = _drawdown_series(equity)
        if max_drawdown is None:
            max_drawdown = float(drawdown.min())
        if not isinstance(max_drawdown_duration, int):
            max_drawdown_duration = _max_drawdown_duration(drawdown)
        recovery_bars, recovered = _recovery(equity, drawdown)
        if recovered is False:
            caveats.add("drawdown_not_recovered")
        if len(returns):
            worst_bar_return = float(np.min(returns))
        worst_month_return = _worst_month(points)
        downside_deviation = _downside_deviation(returns, periods)

    cagr = _finite(stored.get("cagr"))
    calmar = _calmar(cagr, max_drawdown)

    trade_list = [trade for trade in (trades or []) if isinstance(trade, dict)]
    number_of_trades = len(trade_list)
    if number_of_trades == 0:
        caveats.add("no_closed_trades")
    consecutive_losses = stored.get("max_consecutive_losses")
    if not isinstance(consecutive_losses, int):
        consecutive_losses = _max_consecutive_losses(trade_list)

    duration_days: float | None = None
    if isinstance(max_drawdown_duration, int):
        minutes = _BAR_MINUTES.get(timeframe)
        if minutes is not None:
            duration_days = round(max_drawdown_duration * minutes / 1_440.0, 2)

    benchmark = _build_benchmark(
        points=points,
        raw_points=raw_points,
        initial_capital=initial_capital if initial_capital and initial_capital > 0 else None,
        timeframe=timeframe,
        asset_class=asset_class,
        fallback_bars=fallback_bars,
        caveats=caveats,
    )

    total_return = _finite(stored.get("total_return"))
    final_equity = _finite(stored.get("final_equity"))
    excess_return = None
    final_equity_gap = None
    if benchmark is not None:
        if total_return is not None and benchmark["total_return"] is not None:
            excess_return = total_return - benchmark["total_return"]
        if final_equity is not None and benchmark["final_equity"] is not None:
            final_equity_gap = final_equity - benchmark["final_equity"]

    bars = len(points)
    tier = _sample_tier(number_of_trades)
    window_start = next((moment for moment, _ in points if moment is not None), None)
    window_end = next((moment for moment, _ in reversed(points) if moment is not None), None)

    return {
        "run_id": run_id,
        "result_hash": result_hash,
        "analysis_version": ANALYSIS_VERSION,
        "window": {
            "start": _iso(window_start),
            "end": _iso(window_end),
            "bars": bars,
        },
        "performance": {
            "stored": stored,
            "derived": {
                "calmar": _finite(calmar),
                "downside_deviation": _finite(downside_deviation),
                "excess_return": _finite(excess_return),
                "final_equity_gap": _finite(final_equity_gap),
                "worst_bar_return": _finite(worst_bar_return),
            },
        },
        "risk": {
            "max_drawdown": _finite(max_drawdown),
            "max_drawdown_duration_bars": max_drawdown_duration
            if isinstance(max_drawdown_duration, int)
            else None,
            "max_drawdown_duration_days": duration_days,
            "recovery_bars": recovery_bars,
            "recovered": recovered,
            "recovery_text": _recovery_text(recovery_bars, recovered),
            "worst_bar_return": _finite(worst_bar_return),
            "worst_month_return": _finite(worst_month_return),
            "worst_trade": _worst_trade(trade_list),
            "max_consecutive_losses": consecutive_losses
            if isinstance(consecutive_losses, int)
            else None,
            "downside_deviation": _finite(downside_deviation),
        },
        "benchmark": benchmark,
        "sample": {
            "trades": number_of_trades,
            "bars": bars,
            "years": _finite(round(bars / periods, 4)) if bars else None,
            "tier": tier,
            "tier_text": _TIER_TEXT[tier],
        },
        "caveats": caveats.as_list(),
    }


def _recovery_text(bars: int | None, recovered: bool | None) -> str | None:
    if recovered is True and bars is not None:
        return "已恢复到回撤前的高点" if bars else "没有回撤"
    if recovered is False:
        return "到回测结束仍未恢复"
    return None


def _build_benchmark(
    *,
    points: Sequence[tuple[dt.datetime | None, float]],
    raw_points: Sequence[dict[str, Any]],
    initial_capital: float | None,
    timeframe: str,
    asset_class: str | None,
    fallback_bars: Sequence[dict[str, Any]] | None,
    caveats: _Caveats,
) -> dict[str, Any] | None:
    """Buy-and-hold over exactly the strategy's window.

    The stored curve already carries the close of every bar, so the common path
    reads nothing: ``hold_t = initial_capital * close_t / close_0`` uses the same
    bars, the same window and the same calendar as the strategy by construction.
    """

    if initial_capital is None or len(points) < 1:
        caveats.add("benchmark_unavailable")
        return None

    closes = _close_column(raw_points) if len(raw_points) == len(points) else None
    if closes is not None:
        values = _benchmark_curve_from_closes(closes, initial_capital)
        curve = [(moment, value) for (moment, _), value in zip(points, values, strict=True)]
        source = "equity_curve_close"
        window_matched = True
    elif fallback_bars:
        curve, window_matched = _benchmark_curve_from_bars(points, fallback_bars, initial_capital)
        source = "series_bars"
        if not curve:
            caveats.add("benchmark_unavailable")
            return None
        if not window_matched:
            caveats.add("benchmark_partial_window")
    else:
        caveats.add("benchmark_unavailable")
        return None

    bars_matched = len(curve)

    # The comparison runs through the same metric code as the strategy, so
    # "total return" and "Sharpe" mean the same thing on both sides.
    comparison = compute_metrics(
        np.asarray([value for _, value in curve], dtype=float), [], timeframe=timeframe
    )
    if (asset_class or "").lower() == "crypto":
        caveats.add("bars_per_year_252_for_crypto")

    return {
        "label": "买入持有对照",
        "kind": "buy_and_hold",
        "source": source,
        "fees_included": False,
        "window_matched": window_matched,
        "bars_matched": bars_matched,
        "curve": _curve_points(curve),
        "total_return": _finite(comparison.total_return),
        "cagr": _finite(comparison.cagr),
        "annualized_volatility": _finite(comparison.annualized_volatility),
        "sharpe": _finite(comparison.sharpe),
        "max_drawdown": _finite(comparison.max_drawdown),
        "final_equity": _finite(comparison.final_equity),
    }
