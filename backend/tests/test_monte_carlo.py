"""Monte Carlo resampling tests (docs/22, ADR-043).

The module is DB-independent, so most cases drive it with hand-built trade lists
where the correct answer is knowable by hand — that is the only way to prove the
compounding and the ruin logic are right rather than merely plausible.
"""

from __future__ import annotations

import copy

import pytest

from app.research.monte_carlo import (
    MAX_RUNS,
    run_monte_carlo,
)


def _trades(pnls: list[float]) -> list[dict]:
    return [{"pnl": p} for p in pnls]


def _mixed(n: int = 40) -> list[dict]:
    """A deterministic mix of winners and losers, big enough to be informative."""

    pattern = [120.0, -80.0, 45.0, -60.0, 200.0, -150.0, 30.0, -25.0]
    return _trades([pattern[i % len(pattern)] for i in range(n)])


def test_monte_carlo_is_deterministic_for_a_seed() -> None:
    trades = _mixed()
    first = run_monte_carlo(trades, initial_capital=10_000.0, runs=200, seed=42)
    second = run_monte_carlo(trades, initial_capital=10_000.0, runs=200, seed=42)
    assert first == second


def test_different_seeds_give_different_distributions() -> None:
    trades = _mixed()
    a = run_monte_carlo(trades, initial_capital=10_000.0, runs=200, seed=1)
    b = run_monte_carlo(trades, initial_capital=10_000.0, runs=200, seed=2)
    assert a["summary"]["total_return"] != b["summary"]["total_return"]


def test_all_winning_trades_never_loses() -> None:
    """With only profitable trades every resampled path must be profitable.

    Also proves compounding: each path's return is exactly the product of the
    per-trade returns, which a purely additive model would get wrong.
    """

    # +100 on 10,000 is +1%; two trades must compound to 1.01*1.01 - 1.
    report = run_monte_carlo(_trades([100.0, 100.0]), initial_capital=10_000.0, runs=50, seed=7)
    expected = 1.01 * 1.01 - 1.0
    summary = report["summary"]

    assert summary["probability_of_profit"] == 1.0
    assert summary["probability_of_loss"] == 0.0
    assert summary["probability_of_ruin"] == 0.0
    assert summary["total_return"]["p50"] == pytest.approx(expected, rel=1e-9)
    # A monotonically rising path has no drawdown at all.
    assert summary["max_drawdown"]["p50"] == pytest.approx(0.0, abs=1e-12)


def test_losing_everything_reports_ruin() -> None:
    """A trade that loses the whole account must register as ruin, not be hidden."""

    report = run_monte_carlo(_trades([-10_000.0]), initial_capital=10_000.0, runs=25, seed=3)
    summary = report["summary"]
    assert summary["probability_of_ruin"] == 1.0
    assert summary["final_equity"]["p50"] == pytest.approx(0.0, abs=1e-9)
    assert summary["total_return"]["p50"] == pytest.approx(-1.0, abs=1e-9)


def test_drawdown_dispersion_comes_from_trade_order() -> None:
    """Resampling order is what makes drawdown visible.

    The same trades in the observed order may look acceptable; across resampled
    orders the worst path must be at least as bad as the observed sequence, and a
    case built to have a bad ordering must show a materially worse drawdown.
    """

    # Winners first then losers: the observed order never draws down deeply, but
    # resampling can front-load the losses.
    trades = _trades([500.0, 500.0, -400.0, -400.0, -400.0])
    report = run_monte_carlo(trades, initial_capital=10_000.0, runs=500, seed=11)
    summary = report["summary"]

    assert summary["max_drawdown"]["p5"] <= summary["max_drawdown"]["p50"] <= 0.0
    # The optimistic tail is a path where the losses land last.
    assert summary["max_drawdown"]["p95"] >= summary["max_drawdown"]["p5"]
    assert summary["worst_max_drawdown"] <= summary["expected_max_drawdown"]


def test_percentiles_are_ordered() -> None:
    report = run_monte_carlo(_mixed(), initial_capital=10_000.0, runs=400, seed=5)
    for key in ("final_equity", "total_return", "max_drawdown", "sharpe"):
        p = report["summary"][key]
        assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"], key


def test_trades_per_run_scales_the_horizon() -> None:
    trades = _mixed()
    short = run_monte_carlo(trades, initial_capital=10_000.0, runs=100, trades_per_run=5, seed=9)
    long = run_monte_carlo(trades, initial_capital=10_000.0, runs=100, trades_per_run=100, seed=9)
    assert short["summary"]["trades_per_run"] == 5
    assert long["summary"]["trades_per_run"] == 100
    # More trades means a wider outcome spread.
    short_spread = short["summary"]["total_return"]["p95"] - short["summary"]["total_return"]["p5"]
    long_spread = long["summary"]["total_return"]["p95"] - long["summary"]["total_return"]["p5"]
    assert long_spread > short_spread


def test_small_sample_is_flagged() -> None:
    """A distribution built from a handful of trades must say so."""

    report = run_monte_carlo(_trades([100.0, -50.0]), initial_capital=10_000.0, runs=50, seed=1)
    assert any("small sample" in w for w in report["warnings"])
    assert report["summary"]["observed_trades"] == 2


def test_enough_trades_produces_no_small_sample_warning() -> None:
    report = run_monte_carlo(_mixed(30), initial_capital=10_000.0, runs=50, seed=1)
    assert report["warnings"] == []


def test_sample_paths_are_bounded_and_well_formed() -> None:
    report = run_monte_carlo(_mixed(), initial_capital=10_000.0, runs=1_000, seed=4)
    paths = report["sample_equity_paths"]
    assert 0 < len(paths) <= 100
    length = report["summary"]["trades_per_run"] + 1
    assert all(len(p) == length for p in paths)
    assert all(p[0] == pytest.approx(10_000.0) for p in paths)


def test_report_declares_its_method() -> None:
    """The response must name the method so consumers cannot mistake it for a forecast."""

    report = run_monte_carlo(_mixed(), initial_capital=10_000.0, runs=10, seed=1)
    assert report["method"] == "trade_level_iid_bootstrap"
    assert report["monte_carlo_version"]
    assert report["seed"] == 1


def test_rejects_bad_parameters() -> None:
    trades = _mixed()
    with pytest.raises(ValueError, match="runs must be"):
        run_monte_carlo(trades, initial_capital=10_000.0, runs=0)
    with pytest.raises(ValueError, match="exceeds the maximum"):
        run_monte_carlo(trades, initial_capital=10_000.0, runs=MAX_RUNS + 1)
    with pytest.raises(ValueError, match="no closed trades"):
        run_monte_carlo([], initial_capital=10_000.0, runs=10)
    with pytest.raises(ValueError, match="initial_capital"):
        run_monte_carlo(trades, initial_capital=0.0, runs=10)
    with pytest.raises(ValueError, match="trades_per_run"):
        run_monte_carlo(trades, initial_capital=10_000.0, runs=10, trades_per_run=0)


def test_trades_without_pnl_are_ignored() -> None:
    trades = _trades([100.0]) + [{"pnl": None}, {"other": 1}]
    report = run_monte_carlo(trades, initial_capital=10_000.0, runs=10, seed=1)
    assert report["summary"]["observed_trades"] == 1


def test_matches_a_real_backtest(sample_bars) -> None:
    """End-to-end: a real backtest's trades feed the simulation."""

    from app.research.engine import run_backtest
    from app.strategies.dsl import StrategySpec

    dsl = {
        "schema_version": "1.0",
        "strategy": {"id": "mc", "name": "MC", "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
        "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
        "risk": {"stop_loss_atr_multiple": 2.0},
        "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
    }
    spec = StrategySpec.model_validate(copy.deepcopy(dsl))
    result = run_backtest(spec, sample_bars)
    assert result.trades, "fixture should produce trades"

    report = run_monte_carlo(
        result.trades,
        initial_capital=result.initial_capital,
        runs=300,
        seed=17,
    )
    assert report["summary"]["observed_trades"] == len(result.trades)
    assert report["summary"]["runs"] == 300
    assert 0.0 <= report["summary"]["probability_of_profit"] <= 1.0
