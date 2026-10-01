"""Declarative DSL indicators must actually take effect.

Regression target: the validator accepted `indicators[].id` as a known column,
but the feature engine never computed it, so any strategy declaring a custom
indicator crashed at run time. These tests pin the new contract: a declared
indicator becomes a real column (with `period` or a `period_ref` into
`parameters`), is reproducible, and is usable by the executor.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.features.engine import build_features
from app.features.indicators import ema
from app.strategies.dsl import StrategySpec
from app.strategies.executor import run_strategy
from app.strategies.validator import validate_strategy


def _spec(dsl: dict) -> StrategySpec:
    return StrategySpec.model_validate(dsl)


def _custom_dsl(**overrides) -> dict:
    dsl = {
        "schema_version": "1.0",
        "strategy": {"id": "custom", "name": "Custom", "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "indicators": [
            {"id": "fast", "type": "EMA", "period": 10},
            {"id": "slow", "type": "EMA", "period_ref": "slow_period"},
        ],
        "parameters": {"slow_period": 30},
        "entry": {"long": {"all": [{"op": "crosses_above", "left": "fast", "right": "slow"}]}},
        "exit": {"long": {"any": [{"op": "crosses_below", "left": "fast", "right": "slow"}]}},
        "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
        "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
    }
    dsl.update(overrides)
    return dsl


def test_declared_indicators_are_materialized(sample_bars: pd.DataFrame) -> None:
    spec = _spec(_custom_dsl())
    frame = build_features(sample_bars, spec=spec).frame

    assert "fast" in frame.columns
    assert "slow" in frame.columns
    # `fast` uses period 10; `slow` resolves 30 from parameters.slow_period.
    pd.testing.assert_series_equal(frame["fast"], ema(sample_bars["close"], 10), check_names=False)
    pd.testing.assert_series_equal(frame["slow"], ema(sample_bars["close"], 30), check_names=False)


def test_declared_indicators_are_deterministic(sample_bars: pd.DataFrame) -> None:
    spec = _spec(_custom_dsl())
    first = build_features(sample_bars, spec=spec).frame
    second = build_features(sample_bars, spec=spec).frame
    pd.testing.assert_frame_equal(first, second)


def test_validator_accepts_and_executor_uses_custom_indicators(sample_bars: pd.DataFrame) -> None:
    spec = _spec(_custom_dsl())
    report = validate_strategy(spec)
    assert report.is_valid, [i.as_dict() for i in report.errors]
    assert "fast" in report.available_columns and "slow" in report.available_columns

    frame = build_features(sample_bars, spec=spec).frame
    decisions, _intent = run_strategy(spec, frame)
    assert "entry_long" in decisions.columns
    assert decisions["entry_long"].dtype == bool


def test_macd_indicator_exposes_derived_columns(sample_bars: pd.DataFrame) -> None:
    dsl = _custom_dsl(
        indicators=[{"id": "m", "type": "MACD", "params": {"fast": 8, "slow": 17, "signal": 6}}],
        entry={"long": {"all": [{"op": "gt", "left": "m", "right": "m_signal"}]}},
        exit={"long": {"any": [{"op": "lt", "left": "m", "right": "m_signal"}]}},
    )
    spec = _spec(dsl)
    assert validate_strategy(spec).is_valid
    frame = build_features(sample_bars, spec=spec).frame
    assert {"m", "m_signal", "m_hist"}.issubset(frame.columns)


def test_unsupported_indicator_type_is_rejected(sample_bars: pd.DataFrame) -> None:
    spec = _spec(_custom_dsl(indicators=[{"id": "weird", "type": "FOO", "period": 5}]))
    report = validate_strategy(spec)
    assert not report.is_valid
    assert any(i.code == "unsupported_indicator" for i in report.errors)
    with pytest.raises(ValueError):
        build_features(sample_bars, spec=spec)


def test_missing_period_is_rejected() -> None:
    spec = _spec(_custom_dsl(indicators=[{"id": "ema_x", "type": "EMA"}]))
    report = validate_strategy(spec)
    assert any(i.code == "indicator_needs_period" for i in report.errors)


def test_unknown_parameter_reference_is_rejected() -> None:
    spec = _spec(_custom_dsl(indicators=[{"id": "ema_x", "type": "EMA", "period_ref": "nope"}]))
    report = validate_strategy(spec)
    assert any(i.code == "unknown_parameter" for i in report.errors)


def test_run_backtest_with_custom_indicators(sample_bars: pd.DataFrame) -> None:
    from app.research.engine import run_backtest

    spec = _spec(_custom_dsl())
    result = run_backtest(spec, sample_bars, strategy_version="custom@1.0.0")
    assert result.trades is not None
    assert result.engine_version
