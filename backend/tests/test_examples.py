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
from app.strategies.dsl import StrategySpec
from app.strategies.validator import validate_strategy

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "strategies"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_example_strategy_validates_and_backtests(path: Path) -> None:
    spec = StrategySpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    report = validate_strategy(spec)
    assert report.is_valid, [i.as_dict() for i in report.errors]

    provider = get_market_data_provider()
    end = dt.datetime.now(tz=dt.UTC)
    bars = provider.get_ohlcv("DEMO-AAPL", "1d", end - dt.timedelta(days=1200), end)
    assert not bars.empty

    result = run_backtest(
        spec, bars, strategy_version=f"{spec.strategy.id}@{spec.strategy.version}"
    )
    assert result.result_hash
    assert "number_of_trades" in result.metrics
