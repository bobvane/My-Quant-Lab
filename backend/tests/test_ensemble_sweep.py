"""Ensemble vote-threshold sweep (docs/24 §7).

The test that matters most is
``test_sweep_point_matches_a_direct_run_at_the_same_threshold``: the sweep and the
single-threshold endpoint must be the *same arithmetic*, otherwise the chart would
describe an ensemble the user cannot reproduce. Both go through ``_prepare_ensemble``
and ``_run_vote``, so this test is what keeps that true.
"""

from __future__ import annotations

import pytest

from app.research.ensemble import (
    MAX_SWEEP_THRESHOLDS,
    EnsembleMember,
    run_ensemble,
    run_ensemble_sweep,
)
from app.strategies.dsl import StrategySpec


def _spec(strategy_id: str, fast: int, slow: int, **execution: object) -> StrategySpec:
    dsl = {
        "schema_version": "1.0",
        "strategy": {"id": strategy_id, "name": strategy_id, "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "indicators": [
            {"id": "ema_fast", "type": "EMA", "period_ref": "fast_period"},
            {"id": "ema_slow", "type": "EMA", "period_ref": "slow_period"},
        ],
        "parameters": {"fast_period": fast, "slow_period": slow},
        "entry": {
            "long": {"all": [{"op": "crosses_above", "left": "ema_fast", "right": "ema_slow"}]}
        },
        "exit": {
            "long": {"any": [{"op": "crosses_below", "left": "ema_fast", "right": "ema_slow"}]}
        },
        "risk": {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
        "execution": {
            "fee_bps": 10,
            "slippage_bps": 5,
            "initial_capital": 10_000.0,
            **execution,
        },
    }
    return StrategySpec.model_validate(dsl)


def _members(*pairs: tuple[str, int, int], weight: float = 1.0) -> list[EnsembleMember]:
    return [
        EnsembleMember(label=label, spec=_spec(label, fast, slow), weight=weight)
        for label, fast, slow in pairs
    ]


def test_sweep_point_matches_a_direct_run_at_the_same_threshold(sample_bars) -> None:
    """A sweep point must equal the endpoint's answer for that threshold.

    Same members, same bars, same threshold: every headline number has to line up, or
    the chart is describing an ensemble nobody can reproduce.
    """

    members = _members(("a", 5, 40), ("b", 10, 50))
    sweep = run_ensemble_sweep(members, sample_bars, thresholds=[0.5])
    point = next(p for p in sweep["points"] if p["vote_threshold"] == 0.5)

    direct = run_ensemble(members, sample_bars, vote_threshold=0.5)

    assert point["entries_taken"] == direct["agreement"]["entries_taken"]
    assert point["entry_bars"] == direct["agreement"]["entry_bars"]
    assert point["signalled_bars"] == direct["agreement"]["signalled_bars"]
    assert point["solo_signalled_bars"] == direct["agreement"]["solo_signalled_bars"]
    assert point["final_equity"] == pytest.approx(direct["final_equity"])
    assert point["total_return"] == pytest.approx(direct["metrics"]["total_return"])
    assert point["max_drawdown"] == pytest.approx(direct["metrics"]["max_drawdown"])
    assert point["number_of_trades"] == direct["metrics"]["number_of_trades"]
    assert sweep["bars_evaluated"] == direct["bars_evaluated"]
    assert sweep["initial_capital"] == direct["initial_capital"]


def test_sweep_points_are_sorted_by_threshold(sample_bars) -> None:
    sweep = run_ensemble_sweep(
        _members(("a", 5, 40), ("b", 10, 50)), sample_bars, thresholds=[0.5, 0.1, 0.9]
    )
    values = [point["vote_threshold"] for point in sweep["points"]]
    assert values == sorted(values)
    assert sweep["thresholds"] == values


def test_default_thresholds_are_the_coalition_totals(sample_bars) -> None:
    """Equal weights: the only thresholds that change anything are half-votes."""

    sweep = run_ensemble_sweep(_members(("a", 5, 40), ("b", 10, 50)), sample_bars)
    assert sweep["thresholds"] == [0.0, 0.5]
    # 0.0 lets one member through, 0.5 needs both. Those are the two possible answers,
    # so `possible_votes` has to list exactly them (plus the empty coalition).
    assert sweep["possible_votes"] == [0.0, 0.5, 1.0]


def test_effective_vote_names_the_coalition_that_was_waiting(sample_bars) -> None:
    sweep = run_ensemble_sweep(
        _members(("a", 5, 40), ("b", 10, 50), ("c", 15, 60)),
        sample_bars,
        thresholds=[0.3],
    )
    point = sweep["points"][0]
    # Three equal members weigh 1/3 each. A single member already clears 0.3, so the
    # portfolio was really waiting for one member — not for "0.3 worth of agreement",
    # which no combination of votes can produce.
    assert point["effective_vote"] == pytest.approx(1 / 3, abs=1e-6)


def test_weights_change_the_coalition_totals(sample_bars) -> None:
    members = _members(("a", 5, 40), ("b", 10, 50))
    members[1].weight = 3.0  # 0.25 / 0.75 after normalisation
    sweep = run_ensemble_sweep(members, sample_bars)
    assert sweep["thresholds"] == [0.0, 0.25, 0.75]
    assert sweep["possible_votes"] == [0.0, 0.25, 0.75, 1.0]


def test_a_higher_threshold_cannot_open_more_positions(sample_bars) -> None:
    """Monotonicity: requiring more agreement can never let more entries through."""

    sweep = run_ensemble_sweep(
        _members(("a", 5, 40), ("b", 10, 50), ("c", 15, 60)),
        sample_bars,
        thresholds=[0.0, 1 / 3, 0.5, 2 / 3, 0.9],
    )
    entries = [point["entries_taken"] for point in sweep["points"]]
    bars = [point["entry_bars"] for point in sweep["points"]]
    assert entries == sorted(entries, reverse=True)
    assert bars == sorted(bars, reverse=True)


def test_sweep_is_reproducible(sample_bars) -> None:
    members = _members(("a", 5, 40), ("b", 10, 50))
    first = run_ensemble_sweep(members, sample_bars, thresholds=[0.0, 0.5])
    second = run_ensemble_sweep(members, sample_bars, thresholds=[0.0, 0.5])
    assert first["points"] == second["points"]


def test_sweep_reports_member_shares(sample_bars) -> None:
    members = _members(("a", 5, 40), ("b", 10, 50))
    members[1].weight = 3.0
    sweep = run_ensemble_sweep(members, sample_bars, thresholds=[0.5])
    shares = {entry["label"]: entry["weight_share"] for entry in sweep["members"]}
    assert shares == {"a": pytest.approx(0.25), "b": pytest.approx(0.75)}


def test_rejects_empty_thresholds(sample_bars) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run_ensemble_sweep(_members(("a", 5, 40)), sample_bars, thresholds=[])


def test_rejects_too_many_thresholds(sample_bars) -> None:
    thresholds = [(i + 1) / (MAX_SWEEP_THRESHOLDS + 2) for i in range(MAX_SWEEP_THRESHOLDS + 1)]
    with pytest.raises(ValueError, match="at most"):
        run_ensemble_sweep(_members(("a", 5, 40)), sample_bars, thresholds=thresholds)


@pytest.mark.parametrize("threshold", [-0.1, 1.0, 1.5])
def test_rejects_out_of_range_thresholds(sample_bars, threshold: float) -> None:
    with pytest.raises(ValueError, match="must be in \\[0, 1\\)"):
        run_ensemble_sweep(_members(("a", 5, 40)), sample_bars, thresholds=[threshold])


def test_rejects_duplicate_thresholds(sample_bars) -> None:
    with pytest.raises(ValueError, match="duplicates"):
        run_ensemble_sweep(_members(("a", 5, 40)), sample_bars, thresholds=[0.5, 0.5])


def test_rejects_no_members(sample_bars) -> None:
    with pytest.raises(ValueError, match="at least one member"):
        run_ensemble_sweep([], sample_bars)


def test_single_member_sweep_has_no_interior_boundary(sample_bars) -> None:
    """One member carries all the weight, so only the "any signal" threshold exists."""

    sweep = run_ensemble_sweep(_members(("solo", 20, 50)), sample_bars)
    assert sweep["thresholds"] == [0.0]
    assert sweep["possible_votes"] == [0.0, 1.0]
    assert sweep["points"][0]["effective_vote"] == pytest.approx(1.0)
