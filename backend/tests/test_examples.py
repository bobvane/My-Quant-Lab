"""Every bundled example strategy must validate and backtest (DoD: examples).

This mirrors scripts/check_examples.py so CI fails if an example rots — e.g. an
example using a column the validator no longer knows, or a schema field that was
renamed.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from app.data.providers import get_market_data_provider
from app.research.engine import run_backtest
from app.research.sensitivity import run_sensitivity
from app.strategies.dsl import StrategySpec
from app.strategies.validator import validate_strategy

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "strategies"


def _spec(path: Path) -> StrategySpec:
    return StrategySpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _bars():
    provider = get_market_data_provider()
    end = dt.datetime.now(tz=dt.UTC)
    return provider.get_ohlcv("DEMO-AAPL", "1d", end - dt.timedelta(days=1200), end)


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_example_strategy_validates_and_backtests(path: Path) -> None:
    spec = _spec(path)
    report = validate_strategy(spec)
    assert report.is_valid, [i.as_dict() for i in report.errors]

    bars = _bars()
    assert not bars.empty

    result = run_backtest(
        spec, bars, strategy_version=f"{spec.strategy.id}@{spec.strategy.version}"
    )
    assert result.result_hash
    assert "number_of_trades" in result.metrics


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_example_rules_reference_real_indicator_columns(path: Path) -> None:
    """A rule name must resolve to a materialised column, not to a number.

    ``executor._series`` falls back to ``float(name)`` for anything that is not a
    column, and the validator's column check only compares *strings* against the
    known set. Together those hid a real defect: every bundled example declared
    ``indicators[].id = "trend"`` while its rules compared against ``"ema20"``, so
    the comparison was silently against the constant ``20`` and the strategy never
    used its own EMA at all.
    """

    spec = _spec(path)
    frame_columns = set(validate_strategy(spec).available_columns)

    def walk(node: object, where: str, out: list[str]) -> None:
        if node is None:
            return
        left = getattr(node, "left", None)
        right = getattr(node, "right", None)
        if left is not None or right is not None:
            for side, value in (("left", left), ("right", right)):
                if not isinstance(value, str) or value in frame_columns:
                    continue
                # A numeric literal is a legitimate constant threshold; only an
                # identifier-shaped name indicates a column that does not exist.
                try:
                    float(value)
                except ValueError:
                    out.append(f"{where}.{side}={value!r}")
            return
        for group in ("all", "any"):
            for i, child in enumerate(getattr(node, group, None) or []):
                walk(child, f"{where}.{group}[{i}]", out)

    offenders: list[str] = []
    for side in ("long", "short"):
        walk(getattr(spec.entry, side, None), f"entry.{side}", offenders)
        walk(getattr(spec.exit, side, None), f"exit.{side}", offenders)

    assert not offenders, (
        f"{path.name} references names that are not real columns (they would be "
        f"read as constants): {offenders}"
    )


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_example_parameters_are_sweepable(path: Path) -> None:
    """Declared parameters must be referenced by a ``period_ref``.

    This is what makes the bundled examples usable with the sensitivity analysis:
    a parameter nothing reads is decorative, and sweeping it produces identical
    points.
    """

    spec = _spec(path)
    referenced = {ind.period_ref for ind in spec.indicators if getattr(ind, "period_ref", None)}
    assert referenced, f"{path.name} declares no period_ref, so it cannot be swept"

    unused = sorted(set(spec.parameters) - referenced - {"lookback"})
    assert not unused, f"{path.name} declares parameters nothing reads: {unused}"

    bars = _bars()
    axis = sorted(referenced)[0]
    current = int(spec.parameters[axis])
    report = run_sensitivity(spec, bars, grid={axis: [max(2, current - 5), current + 5]})
    assert report["evaluated_points"] >= 1
    hashes = {p["result_hash"] for p in report["points"]}
    assert len(hashes) == 2, f"{path.name} sweep over '{axis}' did not change the result"
