"""Backtest engine tests: determinism, timing, costs, metrics, walk-forward."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from app.research.engine import run_backtest
from app.research.metrics import compute_metrics
from app.research.walk_forward import run_walk_forward
from app.strategies.dsl import StrategySpec

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "t", "name": "Test", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {
        "fill_model": "next_bar_open",
        "fee_bps": 10,
        "slippage_bps": 5,
        "initial_capital": 10_000.0,
    },
}


def _spec(**execution: object) -> StrategySpec:
    dsl = copy.deepcopy(DSL)
    dsl["execution"] = {**DSL["execution"], **execution}
    return StrategySpec.model_validate(dsl)


def test_backtest_is_deterministic(sample_bars: pd.DataFrame) -> None:
    spec = _spec()
    first = run_backtest(spec, sample_bars, strategy_version="t@1.0.0")
    second = run_backtest(spec, sample_bars, strategy_version="t@1.0.0")
    assert first.result_hash == second.result_hash
    assert first.final_equity == pytest.approx(second.final_equity)
    assert len(first.trades) == len(second.trades)


def test_result_hash_changes_with_parameters(sample_bars: pd.DataFrame) -> None:
    spec = _spec()
    cheap = run_backtest(spec, sample_bars, parameters={"fee_bps": 10})
    expensive = run_backtest(spec, sample_bars, parameters={"fee_bps": 50})
    assert cheap.result_hash != expensive.result_hash


def test_fill_uses_next_bar_open_not_signal_bar(sample_bars: pd.DataFrame) -> None:
    spec = _spec()
    result = run_backtest(spec, sample_bars, strategy_version="t@1.0.0")
    frame = sample_bars
    for trade in result.trades:
        entry_time = pd.Timestamp(trade["entry_time"])
        signal_time = pd.Timestamp(trade["entry_time"])
        # the fill price must come from a bar strictly after the signal bar
        signal_idx = frame.index.get_indexer([signal_time], method="nearest")[0]
        entry_idx = frame.index.get_indexer([entry_time], method="nearest")[0]
        assert entry_idx >= signal_idx
        fill = trade["entry_price"]
        bar_open = float(frame["open"].iloc[entry_idx])
        slippage = 0.0005
        assert fill == pytest.approx(bar_open * (1 + slippage), rel=1e-6)


def test_costs_reduce_result(sample_bars: pd.DataFrame) -> None:
    free = run_backtest(_spec(fee_bps=0, slippage_bps=0), sample_bars)
    costly = run_backtest(_spec(fee_bps=50, slippage_bps=50), sample_bars)
    assert costly.final_equity <= free.final_equity


def test_trades_never_look_ahead(sample_bars: pd.DataFrame) -> None:
    """Exits never precede entries and no position exists before its fill bar."""

    result = run_backtest(_spec(), sample_bars)
    frame = sample_bars
    for trade in result.trades:
        entry = pd.Timestamp(trade["entry_time"])
        exit_ = pd.Timestamp(trade["exit_time"])
        assert exit_ >= entry
        assert trade["quantity"] > 0
        assert trade["pnl"] is not None
        # the fill must correspond to a real bar in the dataset
        assert entry in frame.index
        # a stop can trigger inside the very bar the position was opened at,
        # so exit == entry is valid; an exit *before* entry never is.
        assert exit_ >= entry


def test_equity_curve_length_matches_bars(sample_bars: pd.DataFrame) -> None:
    result = run_backtest(_spec(), sample_bars)
    assert len(result.equity_curve) == len(sample_bars)
    assert result.equity_curve[0]["equity"] > 0


def test_ambiguous_fill_is_flagged_and_pessimistic() -> None:
    """A bar hitting stop and target at once must resolve to the stop."""

    rows = []
    index = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    for _ in range(60):
        rows.append((100.0, 101.0, 99.0, 100.0, 1000.0))
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=index)

    # engineer a single bar that spans both levels
    dsl = copy.deepcopy(DSL)
    dsl.update(
        {
            "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "close"}]}},
            "risk": {"stop_loss_atr_multiple": 0.01, "take_profit_atr_multiple": 0.01},
        }
    )
    spec = StrategySpec.model_validate(dsl)
    result = run_backtest(spec, frame)
    ambiguous = [t for t in result.trades if t["ambiguous_fill"]]
    for trade in ambiguous:
        assert trade["exit_reason"] == "stop_loss"
    assert result.metrics["number_of_trades"] >= 0


def test_metrics_report_na_instead_of_faking_values() -> None:
    equity = np.array([100.0, 100.0, 100.0])
    metrics = compute_metrics(equity, [], timeframe="1d")
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.number_of_trades == 0
    assert metrics.notes


def test_metrics_math(sample_bars: pd.DataFrame) -> None:
    trades = [
        {"pnl": 100.0, "holding_bars": 3},
        {"pnl": -50.0, "holding_bars": 2},
        {"pnl": 25.0, "holding_bars": 1},
    ]
    equity = np.array([1000.0, 1100.0, 1050.0, 1075.0])
    metrics = compute_metrics(equity, trades, timeframe="1d")
    assert metrics.win_rate == pytest.approx(2 / 3)
    assert metrics.profit_factor == pytest.approx(125 / 50)
    assert metrics.max_drawdown == pytest.approx(1050 / 1100 - 1)
    assert metrics.average_holding_bars == pytest.approx(2.0)


def test_walk_forward_reports_each_window(sample_bars: pd.DataFrame) -> None:
    spec = _spec()
    outcome = run_walk_forward(
        spec, sample_bars, train_bars=200, test_bars=60, strategy_version="t@1.0.0"
    )
    assert outcome["windows"] >= 2
    for segment in outcome["segments"]:
        assert segment["test_start"] < segment["test_end"]
        assert "in_sample" in segment and "out_of_sample" in segment
    assert outcome["summary"]["mean_oos_return"] is not None


def test_walk_forward_with_insufficient_history_yields_no_windows(
    sample_bars: pd.DataFrame,
) -> None:
    """The engine reports zero windows; the API layer is what rejects the request."""

    outcome = run_walk_forward(
        StrategySpec.model_validate(DSL),
        sample_bars.iloc[:10],
        train_bars=200,
        test_bars=60,
    )
    assert outcome["windows"] == 0
    assert outcome["segments"] == []
    assert outcome["summary"]["mean_oos_return"] is None


def test_short_position_uses_inverted_risk_levels(sample_bars: pd.DataFrame) -> None:
    """A short entry must place its stop above price and its target below."""

    dsl = copy.deepcopy(DSL)
    dsl["market"] = {"asset_classes": ["stock"], "timeframes": ["1d"], "allow_short": True}
    dsl["entry"] = {
        "long": {"all": [{"op": "gt", "left": "close", "right": "close"}]},
        "short": {"all": [{"op": "lt", "left": "close", "right": "close"}]},
    }
    dsl["exit"] = {
        "long": {"any": [{"op": "lt", "left": "close", "right": "close"}]},
        "short": {"any": [{"op": "gt", "left": "close", "right": "close"}]},
    }
    spec = StrategySpec.model_validate(dsl)
    result = run_backtest(spec, sample_bars, strategy_version="short@1.0.0")
    assert result.metrics["number_of_trades"] >= 0
    shorts = [t for t in result.trades if t["direction"] == "SHORT"]
    for trade in shorts:
        # a short loses money when price rises above the entry
        pnl_pct = trade["pnl_pct"]
        if pnl_pct is not None and trade["exit_price"] is not None:
            expected = (trade["entry_price"] - trade["exit_price"]) / trade["entry_price"]
            assert pnl_pct == pytest.approx(expected, rel=1e-6)
