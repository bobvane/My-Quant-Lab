"""Strategy DSL validation and executor tests."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from app.features.engine import build_features
from app.strategies.dsl import StrategySpec
from app.strategies.executor import run_strategy
from app.strategies.validator import validate_strategy

VALID_DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "pa-breakout", "name": "PA Breakout", "version": "1.0.0"},
    "market": {"asset_classes": ["stock", "crypto"], "timeframes": ["1d"]},
    "indicators": [
        {"id": "ema", "type": "EMA", "period": 20, "input": "close"},
        {"id": "atr", "type": "ATR", "period": 14, "input": "ohlc"},
    ],
    "features": ["body_ratio", "breakout"],
    "entry": {
        "long": {
            "all": [
                {"op": "gt", "left": "close", "right": "prior_high"},
                {"op": "gt", "left": "close", "right": "ema20"},
            ]
        }
    },
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def test_valid_dsl_passes_schema_and_validator() -> None:
    spec = StrategySpec.model_validate(VALID_DSL)
    report = validate_strategy(spec)
    assert report.is_valid
    assert not report.errors


def test_unknown_column_is_rejected() -> None:
    dsl = copy.deepcopy(VALID_DSL)
    dsl["entry"] = {"long": {"all": [{"op": "gt", "left": "close", "right": "not_a_column"}]}}
    report = validate_strategy(StrategySpec.model_validate(dsl))
    assert not report.is_valid
    assert any(i.code == "unknown_column" for i in report.errors)


def test_lookahead_reference_is_rejected() -> None:
    dsl = copy.deepcopy(VALID_DSL)
    dsl["entry"] = {"long": {"all": [{"op": "gt", "left": "close", "right": "future_close"}]}}
    report = validate_strategy(StrategySpec.model_validate(dsl))
    assert not report.is_valid
    assert any(i.code == "lookahead_reference" for i in report.errors)


def test_entry_rule_is_mandatory() -> None:
    dsl = copy.deepcopy(VALID_DSL)
    dsl.pop("entry")
    with pytest.raises(ValueError):
        StrategySpec.model_validate(dsl)


def test_risk_block_required_to_be_meaningful() -> None:
    dsl = copy.deepcopy(VALID_DSL)
    dsl["risk"] = {}
    with pytest.raises(ValueError):
        StrategySpec.model_validate(dsl)


def test_zero_cost_warning_is_raised() -> None:
    dsl = copy.deepcopy(VALID_DSL)
    dsl["execution"] = {"fill_model": "next_bar_open", "fee_bps": 0, "slippage_bps": 0}
    report = validate_strategy(StrategySpec.model_validate(dsl))
    assert any(w.code == "zero_costs" for w in report.warnings)


def test_short_entry_requires_opt_in() -> None:
    dsl = copy.deepcopy(VALID_DSL)
    dsl["entry"]["short"] = {"all": [{"op": "lt", "left": "close", "right": "prior_low"}]}
    with pytest.raises(ValueError, match="allow_short"):
        StrategySpec.model_validate(dsl)


def test_executor_is_deterministic(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(VALID_DSL)
    features = build_features(sample_bars).frame
    first, intent_a = run_strategy(spec, features)
    second, intent_b = run_strategy(spec, features)
    pd.testing.assert_frame_equal(first, second)
    assert intent_a == intent_b


def test_executor_no_lookahead_regression(sample_bars: pd.DataFrame) -> None:
    """Signals computed on a longer history must match the truncated history."""

    spec = StrategySpec.model_validate(VALID_DSL)
    long_frame = build_features(sample_bars).frame
    short_frame = build_features(sample_bars.iloc[:300]).frame

    long_out, _ = run_strategy(spec, long_frame)
    short_out, _ = run_strategy(spec, short_frame)

    # compare the overlapping region (up to 300 bars)
    overlap = long_out.iloc[: len(short_out)]
    pd.testing.assert_frame_equal(
        overlap[["entry_long", "exit_long"]],
        short_out[["entry_long", "exit_long"]],
        check_exact=False,
    )


def test_executor_stop_and_target_reference(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(VALID_DSL)
    features = build_features(sample_bars).frame
    out, _ = run_strategy(spec, features)
    valid = out.dropna(subset=["risk_stop", "risk_target"])
    assert not valid.empty
    assert (valid["risk_stop"] < valid["close"]).all()
    assert (valid["risk_target"] > valid["close"]).all()
