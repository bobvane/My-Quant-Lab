"""Fill timing: bar t decides, bar t+1 fills.

Through v1.7.9 the engine got this half right (ADR-116). Entries filled at the next
bar's open, but the stop/target lines -- which ``executor._stop_reference`` derives from
a bar's *close* -- were tested against the high/low of that very bar, reading a level
before the bar that produced it had closed; and a rule exit was filled at the deciding
bar's close, so one strategy entered at t+1's open and left at t's close.

Every frame here is built from flat bars (high 101 / low 99 / close 100), so ATR(14) is
exactly 2.00 and a 2 x ATR stop is exactly 4.00 below the close. That keeps the expected
prices exact instead of approximate.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from app.research.engine import run_backtest
from app.research.ensemble import EnsembleMember, run_ensemble
from app.strategies.dsl import StrategySpec

FEE_BPS = 10.0
SLIP_BPS = 5.0
SLIP = SLIP_BPS / 10_000.0

FLAT = (100.0, 101.0, 99.0, 100.0)

# Fires on every flat bar (close 100 > low 99) and never stops firing: an entry that can
# be aimed at a specific bar without an indicator in the way.
ALWAYS_ENTER = {"op": "gt", "left": "close", "right": "low"}
# `close < low` is never true, so the position can only ever leave through a stop.
NEVER_EXIT = {"op": "lt", "left": "close", "right": "low"}
# Only an up bar closes above its open, only a down bar closes below it.
ENTER_ON_UP_BAR = {"op": "gt", "left": "close", "right": "open"}
EXIT_ON_DOWN_BAR = {"op": "lt", "left": "close", "right": "open"}


def _frame(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [bar[0] for bar in bars],
            "high": [bar[1] for bar in bars],
            "low": [bar[2] for bar in bars],
            "close": [bar[3] for bar in bars],
            "volume": [1_000.0] * len(bars),
        },
        index=pd.date_range("2024-01-01", periods=len(bars), freq="D", tz="UTC"),
    )


def _spec(
    entry: dict[str, Any],
    exit_rule: dict[str, Any],
    *,
    stop_loss_atr_multiple: float,
    take_profit_atr_multiple: float,
) -> StrategySpec:
    return StrategySpec.model_validate(
        {
            "schema_version": "1.0",
            "strategy": {"id": "timing", "name": "Timing", "version": "1.0.0"},
            "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
            "entry": {"long": {"all": [entry]}},
            "exit": {"long": {"any": [exit_rule]}},
            "risk": {
                "stop_loss_atr_multiple": stop_loss_atr_multiple,
                "take_profit_atr_multiple": take_profit_atr_multiple,
            },
            "execution": {
                "fill_model": "next_bar_open",
                "fee_bps": FEE_BPS,
                "slippage_bps": SLIP_BPS,
                "initial_capital": 10_000.0,
            },
        }
    )


def _out_of_reach_spec() -> StrategySpec:
    """A spec whose stop and target are far enough away to never interfere."""

    return _spec(
        ENTER_ON_UP_BAR,
        EXIT_ON_DOWN_BAR,
        stop_loss_atr_multiple=500.0,
        take_profit_atr_multiple=500.0,
    )


def _rule_exit_frame() -> pd.DataFrame:
    """Flat bars, one up bar at 70, and a down bar at 72 that gaps lower on bar 73."""

    bars = [FLAT] * 80
    bars[70] = (100.0, 111.0, 100.0, 110.0)  # closes up: the entry decision
    bars[71] = (110.0, 111.0, 109.0, 110.0)  # the bar that entry fills on
    bars[72] = (110.0, 110.0, 89.0, 90.0)  # closes down: the exit decision
    bars[73] = (80.0, 85.0, 78.0, 84.0)  # the gap the exit has to fill into
    return _frame(bars)


def test_a_stop_is_not_placed_by_the_bar_it_triggers_on() -> None:
    """The stop tested on bar 70 must be the one bar 69 left standing.

    Flat bars put ATR(14) at exactly 2.00, so the 2 x ATR stop sits 4.00 below the
    close: 96.00. Bar 70 collapses to a low of 85.00, which hits it. The *same* bar's
    close (86.00) would put its own stop at 82.00 -- below its low -- so an engine that
    reads the bar it is testing lets the position ride straight through a crash it was
    supposed to be stopped out of.
    """

    bars = [FLAT] * 80
    bars[70] = (100.0, 100.0, 85.0, 86.0)
    frame = _frame(bars)
    result = run_backtest(
        _spec(
            ALWAYS_ENTER,
            NEVER_EXIT,
            stop_loss_atr_multiple=2.0,
            take_profit_atr_multiple=500.0,
        ),
        frame,
    )

    assert result.trades, "the entry rule never produced a position"
    trade = result.trades[0]
    # Entry: decided on bar 50 (the first bar past the feature warm-up), filled at the
    # open of bar 51.
    assert trade["entry_time"] == frame.index[51].isoformat()
    assert trade["entry_price"] == pytest.approx(100.0 * (1 + SLIP), rel=1e-9)
    # Exit: the crash bar, at the stop bar 69 had established.
    assert trade["exit_reason"] == "stop_loss", (
        "the crash bar did not stop the position out: it stayed open to the end of the data"
    )
    assert trade["exit_time"] == frame.index[70].isoformat()
    assert trade["exit_price"] == pytest.approx(96.0 * (1 - SLIP), rel=1e-9)


def test_a_rule_exit_fills_at_the_next_bar_open() -> None:
    """Bar 72 closes below its open, so the exit decided there fills at bar 73's open.

    Bar 73 gaps down to 80.00, which is what the decision is worth; the deciding bar's
    close (90.00) is a price the strategy was never able to trade at.
    """

    frame = _rule_exit_frame()
    result = run_backtest(_out_of_reach_spec(), frame)

    traded = [trade for trade in result.trades if trade["exit_reason"] == "rule_exit"]
    assert traded, "the exit rule never closed the position"
    for trade in traded:
        exit_index = frame.index.get_loc(pd.Timestamp(trade["exit_time"]))
        bar_open = float(frame["open"].iloc[exit_index])
        assert trade["exit_price"] == pytest.approx(bar_open * (1 - SLIP), rel=1e-9)
    assert result.trades[0]["exit_time"] == frame.index[73].isoformat()
    assert result.trades[0]["exit_price"] == pytest.approx(80.0 * (1 - SLIP), rel=1e-9)


def test_an_exit_signal_carries_the_bar_it_decided_on_and_the_bar_it_filled_on() -> None:
    """An exit is an event like an entry: it has a decision bar and a separate fill bar."""

    frame = _rule_exit_frame()
    result = run_backtest(_out_of_reach_spec(), frame)

    exits = [signal for signal in result.signals if signal["direction"] == "FLAT"]
    assert exits, "no exit signal was recorded"
    first = exits[0]
    assert first["state"] == "SELL"
    assert first["bar_time"] == frame.index[72].isoformat()
    assert first["fill_time"] == frame.index[73].isoformat()
    assert first["fill_price"] == pytest.approx(80.0 * (1 - SLIP), rel=1e-9)


def test_an_ensemble_rule_exit_fills_at_the_next_bar_open() -> None:
    """The ensemble portfolio runs its own copy of the loop, so it needs it too."""

    frame = _rule_exit_frame()
    member = EnsembleMember(label="solo", spec=_out_of_reach_spec(), weight=1.0)
    report = run_ensemble([member], frame, vote_threshold=0.0)

    traded = [trade for trade in report["trades"] if trade["exit_reason"] == "rule_exit"]
    assert traded, "the ensemble portfolio never left through its exit rule"
    for trade in traded:
        exit_index = frame.index.get_loc(pd.Timestamp(trade["exit_time"]))
        bar_open = float(frame["open"].iloc[exit_index])
        assert trade["exit_price"] == pytest.approx(bar_open * (1 - SLIP), rel=1e-9)
