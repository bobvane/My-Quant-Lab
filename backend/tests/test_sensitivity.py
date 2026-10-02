"""Parameter sensitivity sweep tests (docs/21, ADR-040).

The sweep is only meaningful because ``run_backtest`` actually applies parameter
overrides. Before ADR-040 it did not, so every grid point came back identical and a
sweep would have reported a perfectly "stable" strategy that was in fact a bug.
"""

from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from app.research.engine import run_backtest
from app.research.sensitivity import (
    MAX_GRID_POINTS,
    expand_grid,
    run_sensitivity,
)
from app.strategies.dsl import StrategySpec

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "sens", "name": "Sensitivity", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "indicators": [{"id": "trend", "type": "EMA", "period_ref": "trend_period"}],
    "parameters": {"trend_period": 20},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "trend"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "trend"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
}


def _spec() -> StrategySpec:
    return StrategySpec.model_validate(copy.deepcopy(DSL))


def test_expand_grid_is_deterministic_cartesian_product() -> None:
    grid = {"a": [1, 2], "b": [10, 20, 30]}
    points = expand_grid(grid)
    assert points == [
        {"a": 1, "b": 10},
        {"a": 1, "b": 20},
        {"a": 1, "b": 30},
        {"a": 2, "b": 10},
        {"a": 2, "b": 20},
        {"a": 2, "b": 30},
    ]
    # Same request, same walk order.
    assert expand_grid(grid) == points


@pytest.mark.parametrize(
    "grid",
    [
        {"a": []},
        {"a": "not-a-list"},
        {"a": 5},
    ],
)
def test_expand_grid_rejects_bad_axes(grid: dict) -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        expand_grid(grid)


def test_expand_grid_of_nothing_is_empty() -> None:
    assert expand_grid({}) == []


def test_sweep_applies_each_grid_point(sample_bars: pd.DataFrame) -> None:
    """Different parameter values must produce genuinely different results."""

    report = run_sensitivity(_spec(), sample_bars, grid={"trend_period": [5, 20, 40]})

    assert report["grid_points"] == 3
    assert report["evaluated_points"] == 3
    # Each point echoes the parameters it was evaluated with.
    assert [p["parameters"] for p in report["points"]] == [
        {"trend_period": 5},
        {"trend_period": 20},
        {"trend_period": 40},
    ]
    # Distinct hashes prove the override reached the engine.
    hashes = {p["result_hash"] for p in report["points"]}
    assert len(hashes) == 3, "grid points collapsed to identical results"


def test_sweep_result_is_reproducible(sample_bars: pd.DataFrame) -> None:
    grid = {"trend_period": [10, 20]}
    first = run_sensitivity(_spec(), sample_bars, grid=grid)
    second = run_sensitivity(_spec(), sample_bars, grid=grid)
    assert first == second
    # And the report survives a JSON round-trip (it is served over the API).
    assert json.loads(json.dumps(first)) == first


def test_sweep_summary_statistics(sample_bars: pd.DataFrame) -> None:
    report = run_sensitivity(_spec(), sample_bars, grid={"trend_period": [5, 10, 20, 40]})
    objectives = [p["objective"] for p in report["points"]]
    assert all(o is not None for o in objectives)

    summary = report["summary"]
    assert summary["min"] == pytest.approx(min(objectives))
    assert summary["max"] == pytest.approx(max(objectives))
    assert summary["range"] == pytest.approx(max(objectives) - min(objectives))
    assert summary["mean"] == pytest.approx(sum(objectives) / len(objectives))
    # best/worst are the actual extremes, and carry their parameters.
    assert report["best"]["objective"] == pytest.approx(summary["max"])
    assert report["worst"]["objective"] == pytest.approx(summary["min"])


def test_sweep_over_multiple_axes(sample_bars: pd.DataFrame) -> None:
    dsl = copy.deepcopy(DSL)
    dsl["indicators"].append({"id": "slow", "type": "SMA", "period_ref": "slow_period"})
    dsl["parameters"]["slow_period"] = 30
    spec = StrategySpec.model_validate(dsl)

    report = run_sensitivity(
        spec, sample_bars, grid={"trend_period": [10, 20], "slow_period": [30, 60]}
    )
    assert report["grid_points"] == 4
    assert set(report["axes"]) == {"trend_period", "slow_period"}

    # Axis order is part of the contract: the *last* grid key varies fastest
    # (itertools.product semantics). The UI derives its heatmap axes from this, so a
    # silent reordering would transpose the chart.
    assert [p["parameters"] for p in report["points"]] == [
        {"trend_period": 10, "slow_period": 30},
        {"trend_period": 10, "slow_period": 60},
        {"trend_period": 20, "slow_period": 30},
        {"trend_period": 20, "slow_period": 60},
    ]


def test_identical_metrics_with_different_parameters_still_change_the_hash(
    sample_bars: pd.DataFrame,
) -> None:
    """A different configuration must hash differently even if the numbers match.

    A coarse parameter can leave the trade list untouched (the same crossings happen
    either way) while still being a different computation. The hash covers the
    *effective parameters*, so it must distinguish them — otherwise two genuinely
    different configurations would look like a single reproducible result.
    """

    dsl = copy.deepcopy(DSL)
    dsl["indicators"].append({"id": "slow", "type": "SMA", "period_ref": "slow_period"})
    dsl["parameters"]["slow_period"] = 30
    spec = StrategySpec.model_validate(dsl)

    report = run_sensitivity(
        spec, sample_bars, grid={"trend_period": [2], "slow_period": [30, 31, 32]}
    )
    first = run_backtest(spec, sample_bars, parameters={"trend_period": 2, "slow_period": 30})
    second = run_backtest(spec, sample_bars, parameters={"trend_period": 2, "slow_period": 31})

    assert len({p["result_hash"] for p in report["points"]}) == 3, (
        "distinct parameter sets collapsed to one hash"
    )
    assert first.result_hash != second.result_hash
    assert report["points"][0]["result_hash"] == first.result_hash


def test_sweep_of_risk_pct_is_an_execution_axis(sample_bars: pd.DataFrame) -> None:
    """``risk_pct`` sweeps position risk, not a strategy parameter (docs/23 §7).

    It lives in ``execution.sizing``, so it must be accepted even though the strategy
    does not declare it — and each point must genuinely change the sizing.
    """

    report = run_sensitivity(
        _spec(),
        sample_bars,
        grid={"risk_pct": [0.005, 0.02, 0.05]},
        metric="total_return",
    )
    assert report["grid_points"] == 3
    assert report["axes"] == {"risk_pct": [0.005, 0.02, 0.05]}
    # Different risk budgets must not collapse to one computation.
    assert len({p["result_hash"] for p in report["points"]}) == 3


def test_risk_pct_sweep_can_be_combined_with_a_parameter(sample_bars: pd.DataFrame) -> None:
    report = run_sensitivity(
        _spec(),
        sample_bars,
        grid={"trend_period": [5, 20], "risk_pct": [0.01, 0.03]},
    )
    assert report["grid_points"] == 4
    assert set(report["axes"]) == {"trend_period", "risk_pct"}
    assert len({p["result_hash"] for p in report["points"]}) == 4


def test_other_sizing_fields_are_not_silently_accepted(sample_bars: pd.DataFrame) -> None:
    """Only the documented execution axes are sweeps; anything else must be rejected.

    ``atr_multiple`` is a real ``execution.sizing`` field but is not an allowed axis, so
    it has to fail as an undeclared parameter rather than quietly doing nothing.
    """

    with pytest.raises(ValueError, match="not declared by the strategy"):
        run_sensitivity(_spec(), sample_bars, grid={"risk_pct": [0.01], "atr_multiple": [2.0]})


def test_unknown_axis_error_mentions_execution_axes(sample_bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="execution axes available"):
        run_sensitivity(_spec(), sample_bars, grid={"nope": [1, 2]})


def test_sweep_rejects_unknown_grid_axis(sample_bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="not declared by the strategy"):
        run_sensitivity(_spec(), sample_bars, grid={"nope": [1, 2]})


def test_sweep_rejects_unsupported_metric(sample_bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="unsupported metric"):
        run_sensitivity(_spec(), sample_bars, grid={"trend_period": [10]}, metric="magic")


def test_sweep_rejects_too_many_points(sample_bars: pd.DataFrame) -> None:
    """The cap protects the worker: a sweep is one backtest per grid point."""

    with pytest.raises(ValueError, match="maximum"):
        run_sensitivity(
            _spec(),
            sample_bars,
            grid={"trend_period": list(range(1, MAX_GRID_POINTS + 2))},
        )


def test_sweep_base_parameters_are_applied(sample_bars: pd.DataFrame) -> None:
    """``base_parameters`` seeds the run; the grid overrides it per point.

    A grid axis and a base value for the *same* key would collide (the grid wins),
    so the base is exercised through a second parameter the grid does not touch.
    """

    dsl = copy.deepcopy(DSL)
    dsl["indicators"].append({"id": "slow", "type": "SMA", "period_ref": "slow_period"})
    dsl["parameters"]["slow_period"] = 30
    spec = StrategySpec.model_validate(dsl)

    default = run_sensitivity(spec, sample_bars, grid={"trend_period": [20]})
    shifted = run_sensitivity(
        spec,
        sample_bars,
        grid={"trend_period": [20]},
        base_parameters={"slow_period": 60},
    )
    assert default["points"][0]["result_hash"] != shifted["points"][0]["result_hash"]


def test_sweep_marks_undefined_objective_as_none() -> None:
    """A grid point with no trades yields a None objective, not a fake zero."""

    index = pd.date_range("2024-01-01", periods=80, freq="D", tz="UTC")
    flat = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1_000.0,
        },
        index=index,
    ).rename_axis("timestamp")

    report = run_sensitivity(_spec(), flat, grid={"trend_period": [10, 20]})
    assert report["evaluated_points"] == 0
    assert all(p["objective"] is None for p in report["points"])
    assert report["summary"]["mean"] is None
    assert report["best"] is None
    assert report["stable"] is None
