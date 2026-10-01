"""Backtest limit/stop entry orders (docs/07 §5, P1)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research.engine import _order_price, _pending_fill, run_backtest
from app.strategies.dsl import StrategySpec

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "ot", "name": "OT", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 0, "slippage_bps": 0},
}


def test_order_price_limit_and_stop() -> None:
    # Long limit buys below the close; long stop buys above it. Short mirrors.
    assert _order_price("limit", 100.0, 2.0, 1.5, 1.0, True) == 97.0
    assert _order_price("limit", 100.0, 2.0, 1.5, 1.0, False) == 103.0
    assert _order_price("stop", 100.0, 2.0, 1.5, 1.5, True) == 103.0
    # Short entries price above the close (strength), whether limit or stop.
    assert _order_price("stop", 100.0, 2.0, 1.5, 1.5, False) == 103.0
    assert np.isnan(_order_price("limit", 100.0, np.nan, 1.5, 1.0, True))


def test_pending_fill_limit_long() -> None:
    pending = {"kind": "limit", "long": True, "price": 97.0}
    # Bar dips below the limit: fill at the limit (min(open, limit)).
    assert _pending_fill(pending, 98.0, 98.5, 96.5) == 97.0
    # Bar never touches the limit.
    assert _pending_fill(pending, 98.0, 98.5, 97.5) is None


def test_pending_fill_stop_long() -> None:
    pending = {"kind": "stop", "long": True, "price": 103.0}
    assert _pending_fill(pending, 102.0, 103.5, 101.0) == 103.0
    assert _pending_fill(pending, 102.0, 102.5, 101.0) is None


def test_pending_fill_limit_short() -> None:
    pending = {"kind": "limit", "long": False, "price": 103.0}
    assert _pending_fill(pending, 102.0, 102.5, 101.0) is None
    # Short limit sells fill at the better (higher) of open and limit.
    assert _pending_fill(pending, 104.0, 104.5, 103.0) == 104.0


def test_run_backtest_with_limit_orders_completes(sample_bars: pd.DataFrame) -> None:
    dsl = dict(_DSL)
    dsl["execution"] = {
        "fill_model": "next_bar_open",
        "entry_order_type": "limit",
        "limit_offset_atr": 0.5,
        "order_valid_bars": 3,
        "fee_bps": 0,
        "slippage_bps": 0,
    }
    spec = StrategySpec.model_validate(dsl)
    result = run_backtest(spec, sample_bars, strategy_version="ot@1.0.0")
    assert result.engine_version
    assert result.trades is not None


def test_validator_requires_offset_for_limit() -> None:
    from app.strategies.validator import validate_strategy

    dsl = dict(_DSL)
    dsl["execution"] = {
        "fill_model": "next_bar_open",
        "entry_order_type": "limit",
        "fee_bps": 0,
        "slippage_bps": 0,
    }
    spec = StrategySpec.model_validate(dsl)
    report = validate_strategy(spec)
    assert any(i.code == "order_needs_offset" for i in report.errors)
