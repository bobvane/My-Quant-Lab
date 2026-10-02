"""Strategy ensemble tests (docs/24, ADR-046).

The most important case here is ``test_votes_actually_drive_the_result``: the first
draft of this module handed a "combined spec" to ``run_backtest``, which recomputes
its own entry/exit flags from the spec — so the vote would have been silently
discarded and the ensemble would have returned one member's result. The test asserts a
combined decision reaches the trades.
"""

from __future__ import annotations

import pytest

from app.research.ensemble import MAX_MEMBERS, EnsembleMember, run_ensemble
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


def _members(*pairs: tuple[str, int, int]) -> list[EnsembleMember]:
    return [
        EnsembleMember(label=label, spec=_spec(label, fast, slow), weight=1.0)
        for label, fast, slow in pairs
    ]


def test_rejects_empty_member_list(sample_bars) -> None:
    with pytest.raises(ValueError, match="at least one member"):
        run_ensemble([], sample_bars)


def test_rejects_too_many_members(sample_bars) -> None:
    members = [
        EnsembleMember(label=f"m{i}", spec=_spec(f"m{i}", 5 + i, 40 + i))
        for i in range(MAX_MEMBERS + 1)
    ]
    with pytest.raises(ValueError, match="at most"):
        run_ensemble(members, sample_bars)


@pytest.mark.parametrize("threshold", [-0.1, 1.0, 1.1])
def test_rejects_bad_threshold(sample_bars, threshold: float) -> None:
    with pytest.raises(ValueError, match="vote_threshold"):
        run_ensemble(_members(("a", 5, 40)), sample_bars, vote_threshold=threshold)


def test_rejects_negative_weights(sample_bars) -> None:
    members = _members(("a", 5, 40), ("b", 10, 50))
    members[1].weight = -1.0
    with pytest.raises(ValueError, match="weights must be >= 0"):
        run_ensemble(members, sample_bars)


def test_rejects_all_zero_weights(sample_bars) -> None:
    members = _members(("a", 5, 40))
    members[0].weight = 0.0
    with pytest.raises(ValueError, match="not all be zero"):
        run_ensemble(members, sample_bars)


def test_single_member_ensemble_runs(sample_bars) -> None:
    report = run_ensemble(_members(("solo", 20, 50)), sample_bars)
    assert report["bars_evaluated"] > 0
    assert report["ensemble_version"]
    assert "metrics" in report
    assert len(report["members"]) == 1
    assert report["members"][0]["weight"] == pytest.approx(1.0)


def test_votes_actually_drive_the_result(sample_bars) -> None:
    """The combined decision series must reach the trades.

    Two very different members are combined; the ensemble's entry count must reflect
    the *intersection* (the vote), not either member's own count. ``run_backtest``
    would have ignored the vote entirely and reproduced the first member.
    """

    members = _members(("fast", 5, 20), ("mid", 10, 30))
    report = run_ensemble(members, sample_bars, vote_threshold=0.5)

    per_member_entries = [m["entry_bars"] for m in report["members"]]
    taken = report["agreement"]["entries_taken"]
    assert taken > 0, "expected agreement somewhere in the fixture"
    # A majority vote can never produce more positions than the least active member.
    assert taken <= min(per_member_entries)
    # Every position the vote opened must be a real, closed trade.
    assert taken == len(report["trades"]), (taken, len(report["trades"]))


def test_majority_needs_more_than_half(sample_bars) -> None:
    """Two equal members must BOTH agree: one alone is exactly half, not a majority.

    Regression: the comparison was inclusive (``>= 0.5``), so a single member cleared
    the bar and the ensemble silently became a union of its members.
    """

    members = _members(("a", 5, 20), ("b", 10, 30))
    report = run_ensemble(members, sample_bars, vote_threshold=0.5)
    per_member = [m["entry_bars"] for m in report["members"]]
    taken = report["agreement"]["entries_taken"]
    # Strict majority of two = the intersection, which cannot exceed either member.
    assert taken <= min(per_member), (taken, per_member)


def test_higher_threshold_is_more_selective(sample_bars) -> None:
    members = _members(("a", 5, 20), ("b", 10, 30), ("c", 12, 35))
    loose = run_ensemble(members, sample_bars, vote_threshold=0.1)
    strict = run_ensemble(members, sample_bars, vote_threshold=0.9)
    # A near-unanimity bar is harder to reach than a bare majority.
    assert strict["agreement"]["entry_bars"] <= loose["agreement"]["entry_bars"]
    assert strict["bars_evaluated"] == loose["bars_evaluated"]


def test_disjoint_members_never_agree(sample_bars) -> None:
    """Two members with no common entry bar cannot produce a strict majority.

    The fixture's 5/20 and 20/60 members have disjoint crossings (AND = 0), so the
    ensemble must open no positions at all — the honest answer, not a union.
    """

    report = run_ensemble(_members(("fast", 5, 20), ("slow", 20, 60)), sample_bars)
    assert report["agreement"]["entry_bars"] == 0
    assert report["agreement"]["entries_taken"] == 0
    assert report["trades"] == []


def test_weights_are_normalised(sample_bars) -> None:
    """[2, 1] and [4, 2] are the same ensemble."""

    a = _spec("a", 5, 20)
    b = _spec("b", 20, 60)
    scaled = run_ensemble([EnsembleMember("a", a, 2.0), EnsembleMember("b", b, 1.0)], sample_bars)
    rescaled = run_ensemble([EnsembleMember("a", a, 4.0), EnsembleMember("b", b, 2.0)], sample_bars)
    assert scaled["members"][0]["weight"] == pytest.approx(rescaled["members"][0]["weight"])
    assert scaled["agreement"] == rescaled["agreement"]
    assert scaled["final_equity"] == pytest.approx(rescaled["final_equity"])


def test_result_is_reproducible(sample_bars) -> None:
    members = _members(("a", 5, 20), ("b", 10, 30))
    first = run_ensemble(members, sample_bars)
    second = run_ensemble(members, sample_bars)
    assert first["agreement"] == second["agreement"]
    assert first["final_equity"] == pytest.approx(second["final_equity"])
    assert first["metrics"] == second["metrics"]


def test_identical_members_reproduce_that_strategy(sample_bars) -> None:
    """If every member is the same strategy, the vote equals that strategy's signal.

    This is the strongest correctness anchor available: with one distinct member
    duplicated, the ensemble must agree on exactly the bars that member fires.
    """

    spec = _spec("dup", 5, 20)
    report = run_ensemble(
        [EnsembleMember("x", spec, 1.0), EnsembleMember("y", spec, 1.0)],
        sample_bars,
        vote_threshold=0.5,
    )
    per_member = report["members"][0]["entry_bars"]
    assert report["agreement"]["entry_bars"] == per_member


def test_portfolio_costs_are_overridable(sample_bars) -> None:
    members = _members(("a", 5, 20), ("b", 10, 30))
    cheap = run_ensemble(members, sample_bars, spec_overrides={"execution": {"fee_bps": 1}})
    pricey = run_ensemble(members, sample_bars, spec_overrides={"execution": {"fee_bps": 100}})
    assert cheap["agreement"] == pricey["agreement"]
    # Same decisions, different costs -> different equity.
    assert cheap["final_equity"] != pytest.approx(pricey["final_equity"])
    assert cheap["final_equity"] > pricey["final_equity"]


def test_risk_sizing_override_reaches_the_portfolio(sample_bars) -> None:
    members = _members(("a", 5, 20), ("b", 10, 30))
    fixed = run_ensemble(members, sample_bars)
    risked = run_ensemble(
        members,
        sample_bars,
        # A small risk budget so the risk-based size is clearly below the 50% fraction
        # and the difference is visible rather than clipped by the cash cap.
        spec_overrides={"execution": {"sizing": {"mode": "risk_per_trade", "risk_pct": 0.001}}},
    )
    assert risked["trades"], "expected the fixture to trade"
    # Same decisions, different size -> different quantities.
    assert fixed["trades"][0]["quantity"] != pytest.approx(risked["trades"][0]["quantity"])
    assert fixed["final_equity"] != pytest.approx(risked["final_equity"])


def test_cash_cap_bounds_risk_sizing(sample_bars) -> None:
    """An aggressive risk budget must not create leverage.

    Checked against the equity of the *bar before* entry. That bar is flat in this
    fixture (the previous position has been closed), so its equity is exactly the cash
    available when the order was sized — no reconstruction from mark prices needed.
    Asserting against the entry bar's equity would be wrong, because that bar marks the
    new position at its close rather than at the fill price.
    """

    report = run_ensemble(
        _members(("a", 5, 20), ("b", 10, 30)),
        sample_bars,
        spec_overrides={"execution": {"sizing": {"mode": "risk_per_trade", "risk_pct": 0.9}}},
    )
    assert report["trades"], "expected the fixture to trade"
    assert report["agreement"]["entry_bars"] == report["agreement"]["entries_taken"]

    equity = [point["equity"] for point in report["equity_curve"]]
    stamps = [point["timestamp"] for point in report["equity_curve"]]
    position = {stamp: i for i, stamp in enumerate(stamps)}

    checked = 0
    for trade in report["trades"]:
        i = position.get(trade["entry_time"])
        if i is None or i == 0:
            continue
        cash_before = equity[i - 1]
        notional = abs(trade["entry_price"] * trade["quantity"])
        # The cap is cash * 0.999; allow a hair for float noise.
        assert notional <= cash_before * 1.001, (notional, cash_before)
        checked += 1
    assert checked == len(report["trades"])


def test_reports_member_warmup_warnings(sample_bars) -> None:
    report = run_ensemble(_members(("a", 5, 20), ("b", 10, 30)), sample_bars)
    assert any("warm-up" in w for w in report["warnings"])


def test_trades_use_next_bar_open(sample_bars) -> None:
    report = run_ensemble(_members(("a", 5, 20), ("b", 10, 30)), sample_bars)
    closes = {point["timestamp"] for point in report["equity_curve"]}
    for trade in report["trades"]:
        assert trade["entry_time"] in closes


def test_unknown_override_key_does_not_crash(sample_bars) -> None:
    members = _members(("a", 5, 20))
    report = run_ensemble(members, sample_bars, spec_overrides={"strategy": {"name": "Combined"}})
    assert report["bars_evaluated"] > 0


def test_identical_members_have_full_support_and_no_solo_signals(sample_bars) -> None:
    """Attribution must be consistent with the degenerate case.

    With one distinct member duplicated, every member signal clears the vote and no bar
    has a "solo" proposal, so the support rate is exactly 1.0 and every solo count is 0.
    A disagreement report that fails here would be describing the vote wrongly.
    """

    spec = _spec("dup", 5, 20)
    report = run_ensemble(
        [EnsembleMember("x", spec, 1.0), EnsembleMember("y", spec, 1.0)],
        sample_bars,
        vote_threshold=0.5,
    )
    agreement = report["agreement"]
    assert agreement["solo_signalled_bars"] == 0
    assert agreement["entry_support_rate"] == pytest.approx(1.0)
    assert agreement["signalled_bars"] == agreement["entry_bars"]
    for member in report["members"]:
        assert member["solo_entries"] == 0
        assert member["entry_agreed"] == member["entry_bars"]
        assert member["entry_support_rate"] == pytest.approx(1.0)
        assert member["vote_agreement_rate"] == pytest.approx(1.0)


def test_plus_one_majority_can_be_a_solo_signal(sample_bars) -> None:
    """Proves the false-agreement case the duplicate check guards against (ADR-046 fix).

    Three members that split two ways plus one: `x` appears twice with weight 0.5 each,
    so `x` alone reaches 1.0 while a genuinely different member reaches only 0.5. The
    vote therefore fires on `x`'s own signals — the report must show a member with a
    full support rate next to one whose proposals were mostly solo and rejected.
    """

    spec_x = _spec("x", 5, 20)
    spec_y = _spec("y", 20, 60)
    report = run_ensemble(
        [
            EnsembleMember("x1", spec_x, 1.0),
            EnsembleMember("x2", spec_x, 1.0),
            EnsembleMember("y", spec_y, 1.0),
        ],
        sample_bars,
        vote_threshold=0.5,
    )
    by_label = {m["label"]: m for m in report["members"]}
    # x1 and x2 are the same strategy, so they always agree with each other.
    assert by_label["x1"]["entry_support_rate"] == pytest.approx(1.0)
    # y shares no crossings with x in this fixture, so nothing it proposes survives.
    assert by_label["y"]["entry_agreed"] == 0
    assert by_label["y"]["solo_entries"] == by_label["y"]["entry_bars"]
    assert by_label["y"]["entry_bars"] > 0, "fixture must give y its own signals to reject"
    # The ensemble fired anyway: it did so on x's signals while rejecting every one of y's.
    assert report["agreement"]["entry_bars"] == by_label["x1"]["entry_bars"]
    # Solo bars are the split votes, so they include all of y's rejected signals (and,
    # in this fixture, the bars where x acted alone as well).
    assert report["agreement"]["solo_signalled_bars"] >= by_label["y"]["entry_bars"]


def test_entry_support_rate_counts_union_not_intersection(sample_bars) -> None:
    members = _members(("a", 5, 20), ("b", 10, 30))
    report = run_ensemble(members, sample_bars, vote_threshold=0.5)
    agreement = report["agreement"]
    assert agreement["signalled_bars"] >= agreement["entry_bars"]
    union = agreement["signalled_bars"]
    if union:
        assert agreement["entry_support_rate"] == pytest.approx(agreement["entry_bars"] / union)
    else:
        assert agreement["entry_support_rate"] is None
    # Every solo bar is a bar where the vote did not fire, so it cannot exceed the union.
    assert agreement["solo_signalled_bars"] <= union
