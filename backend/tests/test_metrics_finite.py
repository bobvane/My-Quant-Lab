"""A ratio the HTTP layer cannot serialize is not a ratio (docs/30 §11).

``compute_metrics`` used to hand out whatever numpy produced: dividing by an
equity point that had reached zero gave ``inf``, and raising a negative ending
equity to a fractional power gave a **complex number**, which ``json.dumps``
refuses outright (``TypeError``), so a perfectly ordinary "the account is
wiped out" curve turned into a 500 on the result endpoint. Every ratio is now
either a finite float or ``None`` -- rendered as 未知, never as 0 -- with the
reason recorded in ``notes``.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from app.research.metrics import BARRS_PER_YEAR, compute_metrics


def _finite_payload(equity: list[float], trades: list[dict] | None = None) -> dict:
    """Compute, then prove every number in the payload can be sent as JSON."""

    metrics = compute_metrics(np.asarray(equity, dtype=float), list(trades or []), timeframe="1d")
    payload = metrics.as_dict()
    for name, value in payload.items():
        if isinstance(value, float):
            assert math.isfinite(value), f"{name} is not finite: {value!r}"
    json.dumps(payload)  # a complex value raises TypeError here
    return payload


def test_a_negative_base_to_a_fractional_power_is_the_bug_being_fixed() -> None:
    """The mechanism, stated directly: this is what used to reach the endpoint."""

    with pytest.raises(TypeError):
        json.dumps({"cagr": (-0.05) ** 0.5})


def test_a_zero_equity_point_does_not_become_an_infinite_return() -> None:
    metrics = compute_metrics(np.array([1000.0, 0.0, 500.0, 400.0]), [], timeframe="1d")

    assert metrics.total_return == pytest.approx(-0.6)
    assert metrics.annualized_volatility is not None
    assert math.isfinite(metrics.annualized_volatility)
    assert any("non-positive equity point" in note for note in metrics.notes)
    _finite_payload([1000.0, 0.0, 500.0, 400.0])


def test_an_equity_curve_that_ends_negative_withholds_cagr() -> None:
    metrics = compute_metrics(np.array([1000.0, 500.0, -50.0]), [], timeframe="1d")

    assert metrics.total_return == pytest.approx(-1.05)
    assert metrics.cagr is None, "a complex CAGR must never leave this module"
    assert any("below zero" in note for note in metrics.notes)
    _finite_payload([1000.0, 500.0, -50.0])


def test_an_overflowing_total_return_is_withheld_not_reported() -> None:
    metrics = compute_metrics(np.array([1e-300, 1e300]), [], timeframe="1d")

    assert metrics.total_return is None
    assert any("total_return" in note for note in metrics.notes)
    _finite_payload([1e-300, 1e300])


def test_a_nan_inside_the_curve_never_reaches_json() -> None:
    payload = _finite_payload([1000.0, float("nan"), 900.0])

    assert payload["total_return"] == pytest.approx(-0.1)


def test_a_healthy_curve_keeps_every_number_it_had_before() -> None:
    """The cleaning must not move a single healthy value: result hashes depend on them."""

    trades = [
        {"pnl": 100.0, "holding_bars": 3},
        {"pnl": -50.0, "holding_bars": 2},
        {"pnl": 25.0, "holding_bars": 1},
    ]
    metrics = compute_metrics(np.array([1000.0, 1100.0, 1050.0, 1075.0]), trades, timeframe="1d")

    assert metrics.win_rate == pytest.approx(2 / 3)
    assert metrics.profit_factor == pytest.approx(125 / 50)
    assert metrics.max_drawdown == pytest.approx(1050 / 1100 - 1)
    assert metrics.average_holding_bars == pytest.approx(2.0)
    assert metrics.total_return == pytest.approx(0.075)
    assert metrics.cagr == pytest.approx((1075.0 / 1000.0) ** (1.0 / (4 / 252.0)) - 1.0)
    # Only the pre-existing sample-size note: cleaning added nothing of its own.
    assert metrics.notes == ["not enough downside samples for Sortino"]


def test_a_flat_curve_still_reports_zero_volatility() -> None:
    metrics = compute_metrics(np.full(5, 1000.0), [], timeframe="1d")

    assert metrics.total_return == 0.0
    assert metrics.annualized_volatility == 0.0
    assert metrics.sharpe is None
    assert metrics.max_drawdown == 0.0


def test_the_timeframe_still_selects_its_own_periods() -> None:
    assert BARRS_PER_YEAR["1d"] == 252.0
    metrics = compute_metrics(np.array([1000.0, 1010.0]), [], timeframe="1w")
    assert metrics.cagr is not None
