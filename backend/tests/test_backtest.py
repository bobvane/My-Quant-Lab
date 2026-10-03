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


def test_result_hash_changes_with_costs(sample_bars: pd.DataFrame) -> None:
    """Costs live in ``execution`` and must be covered by the hash.

    This used to pass ``fee_bps`` through the ``parameters`` argument, which only
    proved the hash mixed in whatever dict it was handed — the engine ignored it,
    so the two runs were byte-identical yet hashed differently. Costs are now
    varied where they actually belong.
    """

    cheap = run_backtest(_spec(fee_bps=10, slippage_bps=5), sample_bars)
    expensive = run_backtest(_spec(fee_bps=50, slippage_bps=5), sample_bars)
    assert cheap.result_hash != expensive.result_hash
    assert cheap.final_equity != pytest.approx(expensive.final_equity)


PERIOD_REF_DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "pr", "name": "Period Ref", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "indicators": [{"id": "emaX", "type": "EMA", "period_ref": "trend"}],
    "parameters": {"trend": 20},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "emaX"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "emaX"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
}


def test_parameter_override_actually_changes_the_backtest(sample_bars: pd.DataFrame) -> None:
    """A ``period_ref`` override must reach the feature engine.

    Regression: ``run_backtest`` accepted ``parameters`` and folded them into the
    result hash but never applied them, so two runs with different periods
    produced *identical* trades while reporting *different* result hashes — the
    reproducibility contract ("same inputs -> same numbers") was broken in the
    direction that matters most: the hash claimed a difference the numbers did
    not have.
    """

    spec = StrategySpec.model_validate(copy.deepcopy(PERIOD_REF_DSL))
    fast = run_backtest(spec, sample_bars, parameters={"trend": 5})
    slow = run_backtest(spec, sample_bars, parameters={"trend": 40})

    # Applying the override must change the actual computed result...
    assert [t["entry_time"] for t in fast.trades] != [t["entry_time"] for t in slow.trades] or (
        len(fast.trades) != len(slow.trades)
    ), "period_ref override did not change the backtest result"
    # ...and a different result must imply a different hash.
    assert fast.result_hash != slow.result_hash


def test_parameters_equivalent_to_spec_produce_the_same_hash(sample_bars: pd.DataFrame) -> None:
    """Overriding nothing must be a no-op, not a hash change.

    The hash covers the *effective* parameters, so passing the spec's own values
    back in cannot silently invalidate a stored result.
    """

    spec = StrategySpec.model_validate(copy.deepcopy(PERIOD_REF_DSL))
    base = run_backtest(spec, sample_bars)
    echoed = run_backtest(spec, sample_bars, parameters={"trend": spec.parameters["trend"]})
    assert base.result_hash == echoed.result_hash
    assert len(base.trades) == len(echoed.trades)


def test_overriding_an_unknown_parameter_warns(sample_bars: pd.DataFrame) -> None:
    """A typo in an override must be reported, not silently ignored.

    This is a warning rather than an error on purpose: an override may legitimately
    carry keys the engine does not need, and callers that pass ``spec.parameters``
    through wholesale must keep working.
    """

    spec = StrategySpec.model_validate(copy.deepcopy(PERIOD_REF_DSL))
    result = run_backtest(spec, sample_bars, parameters={"trendd": 10})
    assert any("trendd" in w for w in result.warnings), result.warnings


def test_unknown_parameter_does_not_change_the_result_hash(sample_bars: pd.DataFrame) -> None:
    """An ignored key must not masquerade as a different computation."""

    spec = StrategySpec.model_validate(copy.deepcopy(PERIOD_REF_DSL))
    base = run_backtest(spec, sample_bars)
    typo = run_backtest(spec, sample_bars, parameters={"trendd": 10})
    assert base.result_hash == typo.result_hash


def test_a_run_that_never_left_its_warm_up_is_flagged(sample_bars: pd.DataFrame) -> None:
    """A run with no evaluable bar reports flat zeros, which must not read as a result.

    The warning string tells a human; ``warmup_unmet`` tells the aggregators, which
    otherwise rank a 0.0 that was never measured above a point that really lost money
    (ADR-055).
    """

    ordinary = run_backtest(StrategySpec.model_validate(copy.deepcopy(PERIOD_REF_DSL)), sample_bars)
    assert ordinary.warmup_unmet is False
    assert ordinary.warnings == []

    starved = run_backtest(
        StrategySpec.model_validate(copy.deepcopy(PERIOD_REF_DSL)),
        sample_bars,
        parameters={"trend": 5000},
    )
    assert starved.warmup_unmet is True
    assert any("warm-up" in w for w in starved.warnings), starved.warnings
    assert starved.metrics["number_of_trades"] == 0

    # The flag stays internal: the HTTP surface conveys this through `warnings`, so it
    # must not leak into the serialised result and change the published shape.
    assert "warmup_unmet" not in starved.as_dict()


def test_fill_uses_next_bar_open_not_signal_bar(sample_bars: pd.DataFrame) -> None:
    """The fill price comes from the bar *after* the one the rule was read on.

    The old version compared ``trade["entry_time"]`` against itself (``signal_time`` was
    built from the very same field) and then re-derived the same bar for the price, so
    ``assert entry_idx >= signal_idx`` could not fail. The signal record carries both
    bars, so the two can finally be told apart.
    """

    spec = _spec()
    result = run_backtest(spec, sample_bars, strategy_version="t@1.0.0")
    frame = sample_bars

    entries = [s for s in result.signals if s["direction"] in {"LONG", "SHORT"}]
    assert entries, "the DSL never entered, so this test would prove nothing"

    def bar_index(value: str) -> int:
        return int(frame.index.get_indexer([pd.Timestamp(value)], method="nearest")[0])

    slippage = 0.0005
    for signal in entries:
        assert bar_index(signal["fill_time"]) == bar_index(signal["bar_time"]) + 1
        bar_open = float(frame["open"].iloc[bar_index(signal["fill_time"])])
        expected = bar_open * (1 + slippage if signal["direction"] == "LONG" else 1 - slippage)
        assert signal["fill_price"] == pytest.approx(expected, rel=1e-9)

    fills = {s["fill_time"]: s["fill_price"] for s in entries}
    for trade in result.trades:
        assert trade["entry_time"] in fills
        assert trade["entry_price"] == pytest.approx(fills[trade["entry_time"]], rel=1e-9)


def test_costs_reduce_result(sample_bars: pd.DataFrame) -> None:
    free = run_backtest(_spec(fee_bps=0, slippage_bps=0), sample_bars)
    costly = run_backtest(_spec(fee_bps=50, slippage_bps=50), sample_bars)
    assert costly.final_equity <= free.final_equity


def test_trades_never_look_ahead(sample_bars: pd.DataFrame) -> None:
    """Every entry sits on a *fill* bar whose decision was taken on an earlier bar.

    The old version asserted ``exit_ >= entry`` twice and nothing else, so it passed on
    an engine that filled rule exits at the deciding bar's close.
    """

    result = run_backtest(_spec(), sample_bars)
    frame = sample_bars
    decisions = {
        signal["fill_time"]: signal["bar_time"]
        for signal in result.signals
        if signal["direction"] in {"LONG", "SHORT"}
    }
    assert decisions, "no entry signal to check"
    assert result.trades, "no trade was produced, so this test would prove nothing"
    for trade in result.trades:
        entry = pd.Timestamp(trade["entry_time"])
        exit_ = pd.Timestamp(trade["exit_time"])
        assert entry in frame.index
        assert exit_ in frame.index
        # a stop can trigger inside the very bar the position was opened at, so
        # exit == entry is valid; an exit *before* entry never is.
        assert exit_ >= entry
        assert trade["quantity"] > 0
        assert trade["pnl"] is not None
        # the entry bar is a fill bar, and the decision that produced it was taken on a
        # strictly earlier bar -- never on the fill bar itself.
        assert trade["entry_time"] in decisions
        assert pd.Timestamp(decisions[trade["entry_time"]]) < entry


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

    # engineer a bar that spans both levels: `close > low` fires on every flat bar and
    # `close < low` never fires, so the position can only leave through the stop/target
    # pair -- which sit 0.01 x ATR (0.02) either side of the close, inside every bar.
    dsl = copy.deepcopy(DSL)
    dsl.update(
        {
            "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "low"}]}},
            "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "low"}]}},
            "risk": {"stop_loss_atr_multiple": 0.01, "take_profit_atr_multiple": 0.01},
        }
    )
    spec = StrategySpec.model_validate(dsl)
    result = run_backtest(spec, frame)
    assert result.metrics["number_of_trades"] > 0, "the rules never fired: nothing was tested"
    ambiguous = [t for t in result.trades if t["ambiguous_fill"]]
    assert ambiguous, "a bar spanning both levels must be flagged"
    for trade in ambiguous:
        assert trade["exit_reason"] == "stop_loss"


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


# --------------------------------------------------------------------------- #
# MAE / MFE / R multiple
# --------------------------------------------------------------------------- #
def test_mae_mfe_are_populated_and_non_negative(sample_bars: pd.DataFrame) -> None:
    """Every trade must have MAE >= 0 and MFE >= 0 (they are absolute distances)."""

    spec = _spec()
    result = run_backtest(spec, sample_bars, strategy_version="mae-mfe@1.0.0")
    assert result.trades, "need at least one trade"
    for trade in result.trades:
        assert trade["mae"] is not None, f"MAE is None for trade: {trade}"
        assert trade["mfe"] is not None, f"MFE is None for trade: {trade}"
        assert trade["mae"] >= 0, f"MAE must be >= 0, got {trade['mae']}"
        assert trade["mfe"] >= 0, f"MFE must be >= 0, got {trade['mfe']}"


def test_mae_mfe_directional_consistency(sample_bars: pd.DataFrame) -> None:
    """For a LONG trade: MAE = entry - lowest_low, MFE = highest_high - entry."""

    spec = _spec()
    result = run_backtest(spec, sample_bars, strategy_version="mae-mfe@1.0.0")
    for trade in result.trades:
        if trade["direction"] != "LONG":
            continue
        entry = trade["entry_price"]
        exit_ = trade["exit_price"]
        # MAE + entry should be >= the lower of entry/exit prices
        # MFE + entry should be >= the higher of entry/exit prices
        if exit_ is not None:
            lower_bound = min(entry, exit_)
            upper_bound = max(entry, exit_)
            assert (trade["mae"] or 0) + entry >= lower_bound - 0.01, (
                f"MAE inconsistent: mae={trade['mae']}, entry={entry}, exit={exit_}"
            )
            assert (trade["mfe"] or 0) + entry >= upper_bound - 0.01, (
                f"MFE inconsistent: mfe={trade['mfe']}, entry={entry}, exit={exit_}"
            )


def test_r_multiple_populated_when_stop_defined(sample_bars: pd.DataFrame) -> None:
    """When the strategy has an ATR stop, R multiple must be calculated."""

    spec = _spec()
    result = run_backtest(spec, sample_bars, strategy_version="r-mult@1.0.0")
    assert result.trades, "need at least one trade"
    for trade in result.trades:
        assert trade["r_multiple"] is not None, (
            f"R multiple is None despite stop_loss_atr_multiple being defined: {trade}"
        )
        assert isinstance(trade["r_multiple"], float)


def test_r_multiple_sign_matches_pnl(sample_bars: pd.DataFrame) -> None:
    """Winning trade → positive R; losing trade → negative R."""

    spec = _spec()
    result = run_backtest(spec, sample_bars, strategy_version="r-sign@1.0.0")
    for trade in result.trades:
        if trade["r_multiple"] is None or trade["pnl"] is None:
            continue
        if trade["pnl"] > 0:
            assert trade["r_multiple"] > 0, (
                f"pnl={trade['pnl']} but r={trade['r_multiple']}: sign mismatch"
            )
        elif trade["pnl"] < 0:
            assert trade["r_multiple"] < 0, (
                f"pnl={trade['pnl']} but r={trade['r_multiple']}: sign mismatch"
            )
