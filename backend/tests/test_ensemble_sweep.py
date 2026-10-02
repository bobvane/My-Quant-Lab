"""Ensemble vote-threshold sweep (docs/24 §7).

The test that matters most is
``test_sweep_point_matches_a_direct_run_at_the_same_threshold``: the sweep and the
single-threshold endpoint must be the *same arithmetic*, otherwise the chart would
describe an ensemble the user cannot reproduce. Both go through ``_prepare_ensemble``
and ``_run_vote``, so this test is what keeps that true.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.research.ensemble import (
    MAX_MEMBERS,
    MAX_SWEEP_THRESHOLDS,
    EnsembleMember,
    _clears,
    _coalition_totals,
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


def _equal_members(count: int) -> list[EnsembleMember]:
    """``count`` equal-weight members with distinct EMA pairs, so none of them is a copy."""

    return _members(*[(f"m{i}", 5 + i, 40 + 2 * i) for i in range(count)])


def test_coalition_totals_do_not_drift_with_member_count() -> None:
    """Twelve equal members are twelfths, not 0.249999 and 0.999996.

    Rounding each partial sum compounded the float error: three twelfths came out as
    0.249999 and twelve as 0.999996. That is cosmetic until you notice 0.999996 < 1.0, at
    which point the fullest possible coalition counts as an interior boundary and the
    default grid contains a threshold that means something no vote total can reach.
    """

    totals = _coalition_totals([1.0 / 12] * 12)
    assert len(totals) == 13
    assert totals[0] == 0.0
    assert totals[-1] == 1.0
    # The thirds and quarters are exactly representable at the reported precision, so a
    # staircase labelled with them is a staircase a reader can type back in.
    assert 0.25 in totals
    assert 0.5 in totals
    assert 0.75 in totals
    assert not [t for t in totals if 0.9999 < t < 1.0]


def test_the_widest_ensemble_can_still_use_the_default_grid(sample_bars) -> None:
    """12 equal members is ``MAX_MEMBERS``; leaving ``thresholds`` blank must work.

    This is the regression: the drifted 0.999996 boundary made the default grid 13 points
    long, one over the cap, so the largest supported ensemble was refused by the endpoint's
    own default.
    """

    sweep = run_ensemble_sweep(_equal_members(MAX_MEMBERS), sample_bars)

    assert len(sweep["thresholds"]) == MAX_MEMBERS
    assert sweep["thresholds"][0] == 0.0
    assert sweep["thresholds"] == sorted(sweep["thresholds"])
    assert sweep["possible_votes"][-1] == 1.0
    assert len(sweep["points"]) == len(sweep["thresholds"])
    assert sweep["max_thresholds"] == MAX_SWEEP_THRESHOLDS

    # Every published step has to be self-consistent: the coalition named as "what the
    # portfolio was waiting for" must be a reachable total and must actually beat the
    # threshold it is reported next to.
    for point in sweep["points"]:
        assert point["vote_threshold"] in sweep["thresholds"]
        assert point["effective_vote"] in sweep["possible_votes"]
        assert point["effective_vote"] > point["vote_threshold"]

    # Twelve equal members are twelve twelfths, so the twelve default steps must name
    # twelve *different* coalitions -- a repeated answer would mean two rows were really
    # the same experiment.
    assert len({point["effective_vote"] for point in sweep["points"]}) == MAX_MEMBERS

    entries = [point["entries_taken"] for point in sweep["points"]]
    assert entries == sorted(entries, reverse=True)


def test_a_default_grid_too_large_to_evaluate_says_what_to_do(sample_bars) -> None:
    """Powers of two make every subset sum distinct, so the exact grid cannot fit.

    The message has to name the size of the grid and the way out, because the caller's
    request was "leave it blank" -- being told only that a sweep supports at most N
    thresholds would not connect the refusal to the thing they asked for.
    """

    members = _equal_members(8)
    for index, member in enumerate(members):
        member.weight = float(2**index)

    with pytest.raises(ValueError) as excinfo:
        run_ensemble_sweep(members, sample_bars)

    message = str(excinfo.value)
    assert "distinct coalition totals" in message
    assert "explicit" in message
    assert str(MAX_SWEEP_THRESHOLDS) in message

    # The same member set is fine when the caller picks the thresholds themselves.
    explicit = run_ensemble_sweep(members, sample_bars, thresholds=[0.0, 0.5])
    assert [point["vote_threshold"] for point in explicit["points"]] == [0.0, 0.5]


def test_a_rounded_boundary_is_still_a_boundary() -> None:
    """A member carrying exactly 1/12 must not clear a threshold reported as 0.083333.

    ``possible_votes`` is rounded for display, so one member's raw 0.08333333333 sits just
    *above* the printed boundary. Without the epsilon the portfolio would open on that vote
    while ``effective_vote`` reported the next coalition up -- the report and the
    simulation disagreeing about which coalition cleared the bar.
    """

    twelfth = np.array([1.0 / 12])
    assert not _clears(twelfth, 0.083333).any()
    assert _clears(twelfth, 0.0).all()
    assert _clears(np.array([2.0 / 12]), 0.083333).all()
    # Equal weights are unaffected: half a vote still needs both members.
    assert not _clears(np.array([0.5]), 0.5).any()
    assert _clears(np.array([1.0]), 0.5).all()
