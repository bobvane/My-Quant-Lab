"""Position sizing modes (docs/23, ADR-045).

Two layers:

* unit tests drive ``_position_quantity`` directly, where the risk arithmetic is
  exactly knowable;
* integration tests run a real backtest and assert the *documented intent* — a
  tighter stop buys more, risk-per-trade really caps the loss near ``risk_pct``
  (which also proves the tighter stop did not simply buy a bigger loss).

The default mode must stay byte-identical to the historical behaviour; that is
covered by the existing backtest/example suites still passing.
"""

from __future__ import annotations

import copy

import pytest

from app.research.engine import _position_quantity, run_backtest
from app.strategies.dsl import SizingSpec, StrategySpec

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "sizing", "name": "Sizing", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0},
    "execution": {"fee_bps": 0, "slippage_bps": 0, "initial_capital": 10_000.0},
}


def _spec(sizing: dict | None = None, **execution: object) -> StrategySpec:
    dsl = copy.deepcopy(DSL)
    dsl["execution"] = {**DSL["execution"], **execution}
    if sizing is not None:
        dsl["execution"]["sizing"] = sizing
    return StrategySpec.model_validate(dsl)


# --------------------------------------------------------------------------- #
# unit: the arithmetic
# --------------------------------------------------------------------------- #


def test_fixed_fraction_spends_the_configured_share() -> None:
    qty = _position_quantity(
        sizing=SizingSpec(mode="fixed_fraction"),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=0.5,
        stop_distance=5.0,
        allow_fractional=True,
    )
    assert qty == pytest.approx(50.0)  # 5000 / 100


def test_fixed_fraction_honours_explicit_fraction_override() -> None:
    qty = _position_quantity(
        sizing=SizingSpec(mode="fixed_fraction", fraction=0.25),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=0.5,
        stop_distance=5.0,
        allow_fractional=True,
    )
    assert qty == pytest.approx(25.0)  # the explicit fraction wins over max_position_pct


def test_risk_per_trade_sizes_from_the_stop_distance() -> None:
    """Risking 1% of 10,000 over a 5-wide stop means 20 units (100 / 5)."""

    qty = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.01),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=1.0,
        stop_distance=5.0,
        allow_fractional=True,
    )
    assert qty == pytest.approx(20.0)
    # Loss at the stop is exactly the risk budget.
    assert qty * 5.0 == pytest.approx(100.0)


def test_risk_per_trade_buys_less_when_the_stop_is_wider() -> None:
    wide = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.01),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=1.0,
        stop_distance=20.0,
        allow_fractional=True,
    )
    tight = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.01),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=1.0,
        stop_distance=2.0,
        allow_fractional=True,
    )
    assert tight > wide
    assert tight * 2.0 == pytest.approx(100.0)
    assert wide * 20.0 == pytest.approx(100.0)


def test_risk_based_size_is_capped_by_available_cash() -> None:
    """A very tight stop must not create leverage."""

    qty = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.5),
        cash=1_000.0,
        fill=100.0,
        max_position_pct=1.0,
        stop_distance=0.5,
        allow_fractional=True,
    )
    notional = qty * 100.0
    assert notional <= 1_000.0
    assert qty == pytest.approx(9.99)  # capped to cash*0.999/fill


def test_risk_mode_falls_back_to_fraction_without_a_stop() -> None:
    """No usable stop distance must not mean 'skip the trade'."""

    qty = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.01),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=0.5,
        stop_distance=None,
        allow_fractional=True,
    )
    assert qty == pytest.approx(50.0)

    zero = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.01),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=0.5,
        stop_distance=0.0,
        allow_fractional=True,
    )
    assert zero == pytest.approx(50.0)


def test_non_fractional_truncates_to_whole_units() -> None:
    qty = _position_quantity(
        sizing=SizingSpec(mode="risk_per_trade", risk_pct=0.01),
        cash=10_000.0,
        fill=100.0,
        max_position_pct=1.0,
        stop_distance=3.0,
        allow_fractional=False,
    )
    assert qty == float(int(qty))
    assert qty == 33.0  # 100/3 = 33.33 -> 33


def test_zero_inputs_produce_no_position() -> None:
    for kwargs in (
        {"cash": 0.0},
        {"fill": 0.0},
        {"max_position_pct": 0.0},
    ):
        base = {
            "sizing": SizingSpec(mode="fixed_fraction"),
            "cash": 10_000.0,
            "fill": 100.0,
            "max_position_pct": 1.0,
            "stop_distance": 5.0,
            "allow_fractional": True,
        }
        base.update(kwargs)
        assert _position_quantity(**base) == 0.0


# --------------------------------------------------------------------------- #
# integration: a real backtest
# --------------------------------------------------------------------------- #


def test_default_sizing_is_fixed_fraction(sample_bars) -> None:
    spec = _spec()
    assert spec.execution.sizing.mode == "fixed_fraction"
    # An explicit fixed_fraction spec must produce the same run as the default.
    explicit = _spec({"mode": "fixed_fraction"})
    a = run_backtest(spec, sample_bars)
    b = run_backtest(explicit, sample_bars)
    assert a.result_hash == b.result_hash
    assert a.final_equity == pytest.approx(b.final_equity)


def test_risk_per_trade_changes_the_outcome(sample_bars) -> None:
    """Sizing is part of the computation, so it must reach the result."""

    fixed = run_backtest(_spec(), sample_bars)
    risked = run_backtest(_spec({"mode": "risk_per_trade", "risk_pct": 0.01}), sample_bars)
    assert fixed.trades, "fixture should trade"
    assert fixed.result_hash != risked.result_hash
    assert fixed.trades[0]["quantity"] != pytest.approx(risked.trades[0]["quantity"])


def test_tighter_stop_buys_more_and_risks_the_same(sample_bars) -> None:
    """The documented intent of risk-based sizing, end to end.

    Halving the ATR multiple halves the stop distance, so the position buys more —
    and the loss taken at the stop stays near risk_pct either way. That second
    assertion is the one that matters: it rules out "bought more because it
    multiplied the loss".

    The pair is 1.5/0.75 rather than the DSL's 2.0/1.0 because since ADR-116 the stop
    tested on a bar is the line frozen at the entry decision, and that line trails the
    close (``close[i-1] - n * atr[i-1]``). On this fixture a 2 ATR stop is almost never
    reached before the strategy's own exit rule closes the trade, so the wide side
    would have no stop-out to compare. The tighter side is also capped by available
    cash (``affordable``) on this fixture, which is why the first assertion is "more",
    not "double".
    """

    def _risked(multiple: float):
        dsl = copy.deepcopy(DSL)
        dsl["risk"] = {"stop_loss_atr_multiple": multiple}
        dsl["execution"] = {
            **DSL["execution"],
            "sizing": {"mode": "risk_per_trade", "risk_pct": 0.02},
        }
        return run_backtest(StrategySpec.model_validate(dsl), sample_bars)

    wide = _risked(1.5)
    # Same strategy, but the stop is half as far away.
    tight = _risked(0.75)

    assert wide.trades and tight.trades
    assert tight.trades[0]["quantity"] > wide.trades[0]["quantity"]

    def stop_losses(result):
        return [
            abs(t["pnl"]) for t in result.trades if t["exit_reason"] == "stop_loss" and t["pnl"] < 0
        ]

    wide_risks = stop_losses(wide)
    tight_risks = stop_losses(tight)
    assert wide_risks and tight_risks, "expected stop-loss exits in the fixture"

    capital = float(DSL["execution"]["initial_capital"])
    budget = 0.02 * capital
    # Each stop-out should cost roughly the risk budget (never multiples of it).
    for risk in wide_risks + tight_risks:
        assert risk < budget * 3.0, f"stop loss {risk} far exceeds the risk budget {budget}"


def test_atr_risk_mode_runs_and_sizes_from_the_stop(sample_bars) -> None:
    fixed = run_backtest(_spec(), sample_bars)
    atr = run_backtest(_spec({"mode": "atr_risk", "risk_pct": 0.02}), sample_bars)
    assert atr.result_hash != fixed.result_hash
    assert atr.trades


def test_sizing_appears_in_the_execution_snapshot() -> None:
    """The stored execution model must record how positions were sized."""

    spec = _spec({"mode": "risk_per_trade", "risk_pct": 0.03})
    dumped = spec.execution.model_dump()
    assert dumped["sizing"]["mode"] == "risk_per_trade"
    assert dumped["sizing"]["risk_pct"] == pytest.approx(0.03)


@pytest.mark.parametrize("bad", [0.0, -0.01, 1.5])
def test_rejects_invalid_risk_pct(bad: float) -> None:
    with pytest.raises(ValueError):
        SizingSpec(mode="risk_per_trade", risk_pct=bad)


def test_sizing_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        SizingSpec(mode="kelly")  # type: ignore[arg-type]
