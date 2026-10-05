"""Step 2B: the deterministic Strategy Compiler Core (docs/29 §18, T13–T21).

`docs/29_STRATEGY_COMPILER_CONTRACT.md` states what the compiler must do, and
`backend/tests/test_compiler_contract.py` guards that document. This module guards the
implementation in `backend/app/compiler/`.

Every case is a pure call: no database, no network, no clock, no model, no backtest.
The two fixtures that come from outside are passed in as plain data — the Martin payload
from `tests/research_payloads.py`, and the legal example strategies under
`examples/strategies/`.
"""

from __future__ import annotations

import copy
import inspect
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from app.ai.research_schemas import CapabilityDecision
from app.compiler import (
    REJECTION_CODES,
    USER_DECIDABLE_CODES,
    CompileResult,
    compile_strategy_draft,
)
from app.compiler.mapping import DRIFTED_DERIVED_COLUMNS, ENGINE_FEATURE_COLUMNS
from app.data.strategy_service import parse_spec
from app.features.catalogue import FEATURE_CATALOGUE
from app.strategies.validator import BASE_COLUMNS, KNOWN_DERIVED, validate_strategy
from tests.research_payloads import draft_payload

MARTIN_CODES = {
    "unknown_blocks_slot",
    "missing_required_slot",
    "rule_unmapped",
    "parameter_invalid",
    "not_expressible",
}

#: §13.2 (Step 2B Final Corrective Pass): the derived names `validate_strategy` accepts
#: but `build_features` never materialises at v2.1.0. Pinned here on purpose — if the
#: engine grows one of these columns the set shrinks, `DRIFTED_DERIVED_COLUMNS` follows
#: on its own, and this constant is what makes a human notice the change.
DRIFTED_COLUMNS = (
    "highest_high_20",
    "lowest_low_20",
    "previous_high",
    "previous_low",
    "rolling_high_prev",
    "rolling_low_prev",
    "rsi",
)

# docs/29 §17.2 froze this digest: the §15.2 projection of the Martin fixture.
MARTIN_DRAFT_HASH = "b736374722d905936044135d10fa1039d87fb80b067ba932557dd65d414121f3"

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "strategies"


def _base_draft() -> dict[str, Any]:
    """A small strategy that already carries every economic decision the contract wants.

    RSI(14) below 30 buys, RSI(14) above 70 sells, ATR stop, half position, fixed
    fraction sizing, explicit costs. Everything the compiler is forbidden to invent is
    written down here, so a clean compile means the compiler invented nothing.
    """

    return {
        "strategy_name": "RSI mean reversion",
        "status": "SUPPORTED",
        "market": {
            "markets": [],
            "asset_classes": ["stock"],
            "timeframes": ["1d"],
            "universe": None,
        },
        "rules": [
            {
                "id": "e-oversold",
                "field": "entry",
                "statement": "buy when RSI(14) drops below 30",
                "origin": "EXPLICIT",
                "parameters": {
                    "left": "RSI",
                    "operator": "lt",
                    "right": 30,
                    "period": 14,
                    "side": "long",
                },
            },
            {
                "id": "x-overbought",
                "field": "exit",
                "statement": "sell when RSI(14) rises above 70",
                "origin": "EXPLICIT",
                "parameters": {
                    "left": "RSI",
                    "operator": "gt",
                    "right": 70,
                    "period": 14,
                    "side": "long",
                },
            },
            {
                "id": "k-risk",
                "field": "risk",
                "statement": "two ATR of stop, half of the account",
                "origin": "EXPLICIT",
                "parameters": {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
            },
            {
                "id": "s-sizing",
                "field": "sizing",
                "statement": "a third of the account at a time",
                "origin": "EXPLICIT",
                "parameters": {"mode": "fixed_fraction", "fraction": 0.3},
            },
            {
                "id": "c-costs",
                "field": "execution",
                "statement": "ten basis points of fee and five of slippage",
                "origin": "EXPLICIT",
                "parameters": {"fee_bps": 10, "slippage_bps": 5},
            },
        ],
        "indicators": [
            {"name": "RSI", "origin": "EXPLICIT", "parameters": {"period": 14, "input": "close"}}
        ],
        "unknowns": [],
        "required_capabilities": [],
        "experimental_alternatives": [],
        "assumptions": [],
        "parameters": {},
        "notes": [],
        "understanding_of_original": "An RSI mean reversion strategy.",
    }


def _codes(result: CompileResult) -> list[str]:
    return list(result.codes)


def _user_required_slots(result: CompileResult) -> list[str]:
    return [
        slot["slot"] for slot in result.report["slots"] if slot["decided_by"] == "USER_REQUIRED"
    ]


def _frozen(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _rejection_trace(result: CompileResult) -> list[tuple[str, str, str]]:
    return [(item["code"], item["slot"], item["detail"]) for item in result.report["rejections"]]


def _conditions(nodes: Any) -> list[dict[str, Any]]:
    """The three fields of a mapped condition, in the spec's own spelling (§10.1)."""

    return [{"op": node["op"], "left": node["left"], "right": node["right"]} for node in nodes]


# ------------------------------------------------------------------ T13 prose


def test_statement_changes_never_change_the_spec():
    """T13: prose is trace-only (docs/29 §6.2, §15.2)."""

    quiet = copy.deepcopy(_base_draft())
    loud = copy.deepcopy(_base_draft())
    loud["understanding_of_original"] = "A completely different sentence about something else."
    loud["notes"] = ["a note that did not exist before"]
    for rule in loud["rules"]:
        rule["statement"] = "REWRITTEN: " + rule["statement"]
        rule["note"] = "an extra note"
    for indicator in loud["indicators"]:
        indicator["note"] = "an extra indicator note"

    quiet_result = compile_strategy_draft(quiet, 3, "1.0.0")
    loud_result = compile_strategy_draft(loud, 3, "1.0.0")

    assert quiet_result.result == loud_result.result == "COMPILED"
    assert quiet_result.draft_hash == loud_result.draft_hash
    assert quiet_result.compile_hash == loud_result.compile_hash
    assert quiet_result.as_dsl() == loud_result.as_dsl()
    # the rewritten statement is still reported verbatim, so a human can trace it
    statements = [item["statement"] for item in loud_result.report["rules"]]
    assert "REWRITTEN: buy when RSI(14) drops below 30" in statements


def test_martin_prose_changes_keep_the_same_draft_hash():
    """T13 on the real fixture: no amount of wording moves the hash."""

    noisy = copy.deepcopy(draft_payload())
    noisy["understanding_of_original"] = "different"
    for rule in noisy["rules"]:
        rule["statement"] = "different"
    for unknown in noisy["unknowns"]:
        unknown["why"] = "different"

    plain_result = compile_strategy_draft(draft_payload(), 7, "1.0.0")
    noisy_result = compile_strategy_draft(noisy, 7, "1.0.0")

    assert plain_result.draft_hash == noisy_result.draft_hash == MARTIN_DRAFT_HASH
    assert _codes(plain_result) == _codes(noisy_result)


# ---------------------------------------------------------- T14 canonical shape


def test_illegal_parameter_shape_is_refused_with_a_hint():
    """T14: `{"indicator": ..., "threshold": ...}` is never repaired (docs/29 §10.2)."""

    payload = _base_draft()
    payload["rules"][0]["parameters"] = {"indicator": "RSI", "period": 14, "threshold": 30}

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result != "COMPILED"
    assert result.spec is None
    assert "parameter_invalid" in _codes(result)
    details = [item["detail"] for item in result.report["rejections"]]
    assert any("canonical form of this rule is" in detail for detail in details)
    # the hint shows `indicator` -> `left` and `threshold` -> `right`, so the fix is obvious
    assert any('"left": "RSI"' in detail and '"right": 30' in detail for detail in details)
    # the refused rule still owns a report entry, marked as refused
    entry = next(item for item in result.report["rules"] if item["draft_rule_id"] == "e-oversold")
    assert entry["decision"] == "REJECTED"


def test_a_numeric_string_threshold_is_refused():
    """§10.3: `right` is a number or an operand name, never a numeric string."""

    payload = _base_draft()
    payload["rules"][0]["parameters"]["right"] = "30"

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert "parameter_invalid" in _codes(result)
    assert any(
        "JSON number" in item["detail"]
        for item in result.report["rejections"]
        if item["code"] == "parameter_invalid"
    )


def test_an_indicator_declaration_produces_no_condition():
    """§10.6 declaration form: it declares an indicator, it does not place a condition."""

    payload = _base_draft()
    payload["rules"] = [rule for rule in payload["rules"] if rule["field"] != "entry"] + [
        {
            "id": "i-declare",
            "field": "indicator",
            "statement": "use RSI(14)",
            "origin": "EXPLICIT",
            "parameters": {"name": "RSI", "period": 14},
        }
    ]

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert "entry.long" in _user_required_slots(result)
    assert "missing_required_slot" in _codes(result)
    # and the input column stays unsaid: a declaration no condition points at owes nothing
    assert not any(slot.endswith(".input") for slot in _user_required_slots(result))


def test_a_condition_period_defines_an_undeclared_indicator():
    """§10.1's canonical example carries the period, so the period is answered."""

    payload = _base_draft()
    payload["rules"][0]["parameters"].update({"left": "EMA", "period": 20, "right": 30})

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    # the column an indicator is computed on is still a decision (§12), the period is not
    assert _user_required_slots(result) == ["indicators.ema20.input"]
    slots = [slot["slot"] for slot in result.report["slots"]]
    assert "indicators.ema20.period" not in slots
    assert "indicators.ema20" in slots


def test_a_condition_period_that_contradicts_the_declaration_is_a_conflict():
    """§10.3: a rule-level `period` that disagrees with `indicators[]` is `rule_conflict`."""

    payload = _base_draft()
    payload["rules"][0]["parameters"]["period"] = 50

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert "rule_conflict" in _codes(result)
    assert any(
        "period 14" in item["detail"] and "says 50" in item["detail"]
        for item in result.report["rejections"]
        if item["code"] == "rule_conflict"
    )


def test_a_declaration_without_a_period_is_a_question():
    """§12: a periodic indicator with no period is not silently given one."""

    payload = _base_draft()
    payload["indicators"] = [
        {"name": "RSI", "origin": "EXPLICIT", "parameters": {"input": "close"}}
    ]

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert "missing_required_slot" in _codes(result)
    assert "indicators.rsi" in _user_required_slots(result)


# ------------------------------------------------- T15 missing economic decision


def test_a_missing_economic_decision_asks_the_user():
    """T15: the DSL default is not an answer (docs/29 §12)."""

    payload = _base_draft()
    payload["rules"][2]["parameters"] = {"stop_loss_atr_multiple": 2.0}

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert result.spec is None
    assert _user_required_slots(result) == ["risk.max_position_pct"]
    assert "missing_required_slot" in _codes(result)
    # the engine's full-position default (1.0) must not appear as a decided value
    decided = [slot for slot in result.report["slots"] if slot["slot"] == "risk.max_position_pct"]
    assert decided[0]["value"] is None
    assert decided[0]["decided_by"] == "USER_REQUIRED"


def test_a_missing_cost_is_not_free():
    """T15: `fee_bps`/`slippage_bps` default to 0.0 in the DSL, and that is a decision."""

    payload = _base_draft()
    payload["rules"][4]["parameters"] = {"fee_bps": 10}

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert _user_required_slots(result) == ["execution.slippage_bps"]


def test_a_missing_sizing_mode_is_not_fixed_fraction():
    payload = _base_draft()
    payload["rules"] = [rule for rule in payload["rules"] if rule["field"] != "sizing"]

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert _user_required_slots(result) == ["sizing.mode"]


def test_a_risk_block_without_a_stop_or_target_is_refused():
    """§11.2: the DSL requires a stop or a take profit, so a lone position cap blocks."""

    payload = _base_draft()
    payload["rules"][2]["parameters"] = {"max_position_pct": 0.5}

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert "risk" in _user_required_slots(result)
    assert "missing_required_slot" in _codes(result)


def test_nested_risk_and_unitless_take_profit_are_refused():
    """§11.2: only the flat, unit-carrying risk keys are canonical."""

    nested = _base_draft()
    nested["rules"][2]["parameters"] = {
        "stop_loss": {"type": "atr_multiple", "multiple": 2.0},
        "max_position_pct": 0.5,
    }
    unitless = _base_draft()
    unitless["rules"][2]["parameters"] = {"take_profit": 2.0, "max_position_pct": 0.5}

    for payload in (nested, unitless):
        result = compile_strategy_draft(payload, 3, "1.0.0")
        # the risk rule itself is refused, so no flat key survives to be asked about
        assert result.result == "REJECTED"
        assert result.spec is None
        assert "parameter_invalid" in _codes(result)


# ------------------------------------------------------------------- T16 Martin


def test_martin_stays_uncompilable():
    """T16: the negative acceptance test of the whole contract (docs/29 §17)."""

    result = compile_strategy_draft(draft_payload(), 7, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert result.spec is None
    assert result.as_dsl() is None
    assert result.compile_hash is None
    assert set(_codes(result)) == MARTIN_CODES
    assert result.draft_hash == MARTIN_DRAFT_HASH
    # ADR-168: the code stays in the vocabulary but has no input to fire on
    assert "ambiguous_phrase" in result.report["unreachable_in_step_2b"]
    assert "ambiguous_phrase" not in _codes(result)
    # one rejection trace per trigger, never one per code (§9.2)
    triggers = [(item["code"], item["slot"]) for item in result.report["rejections"]]
    assert len(triggers) == len(set(triggers))
    assert ("not_expressible", "market.universe") in triggers
    assert ("not_expressible", "market.markets") in triggers
    # the report may quote the draft; nothing was invented to make BTC work
    assert "symbol" not in _frozen(result.report)


def test_the_unreachable_map_is_exactly_the_adr_168_boundary():
    """ADR-168: the three legal codes the frozen input cannot reach are named, with reasons."""

    result = compile_strategy_draft(draft_payload(), 7, "1.0.0")

    unreachable = result.report["unreachable_in_step_2b"]
    assert set(unreachable) == {"ambiguous_phrase", "provenance_invalid", "version_conflict"}
    for code, reason in unreachable.items():
        assert reason, f"{code} must carry the reason it cannot fire"
    # The fallback code is *not* the same kind of unavailability: it is a live code with
    # no emission point yet, so it must stay out of the input-boundary map (docs/29 §9.1).
    assert "needs_user_decision" not in unreachable
    assert "needs_user_decision" in REJECTION_CODES
    assert "needs_user_decision" in USER_DECIDABLE_CODES
    assert "needs_user_decision" not in _codes(result)


# ----------------------------------------------------------------- T17 universe


def test_universe_never_becomes_a_symbol():
    """T17: `market.universe`/`markets` have no DSL slot, so they are `not_expressible`."""

    payload = _base_draft()
    payload["market"]["universe"] = "BTC"
    payload["market"]["markets"] = ["crypto"]

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "REJECTED"
    assert _codes(result) == ["not_expressible"]
    slots = [item["slot"] for item in result.report["rejections"]]
    assert slots == ["market.universe", "market.markets"]
    assert result.spec is None
    assert "symbol" not in _frozen(result.report)


# --------------------------------------------------------------- T18 collision


def test_two_declarations_of_the_same_indicator_are_a_collision():
    """T18: no silent rename, no `rsi14_2` (docs/29 §10.5)."""

    payload = _base_draft()
    payload["rules"].append(
        {
            "id": "i-declare",
            "field": "indicator",
            "statement": "declare RSI(14) again",
            "origin": "EXPLICIT",
            "parameters": {"name": "RSI", "period": 14},
        }
    )

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "REJECTED"
    assert "indicator_collision" in _codes(result)
    assert result.spec is None
    indicator_slots = [
        slot["slot"] for slot in result.report["slots"] if slot["slot"].startswith("indicators.")
    ]
    assert indicator_slots.count("indicators.rsi14") == 1
    assert not any("_2" in slot for slot in indicator_slots)


def test_two_periods_of_the_same_indicator_are_two_indicators():
    """The other half of T18: EMA(20) and EMA(50) are not a collision."""

    payload = _base_draft()
    payload["indicators"] = [
        {"name": "EMA", "origin": "EXPLICIT", "parameters": {"period": 20, "input": "close"}},
        {"name": "EMA", "origin": "EXPLICIT", "parameters": {"period": 50, "input": "close"}},
    ]
    payload["rules"][0]["parameters"] = {
        "left": "ema20",
        "operator": "crosses_above",
        "right": "ema50",
        "side": "long",
    }
    payload["rules"][1]["parameters"] = {
        "left": "ema20",
        "operator": "crosses_below",
        "right": "ema50",
        "side": "long",
    }

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "COMPILED", _rejection_trace(result)
    spec = result.as_dsl()
    assert [(item["id"], item["period"]) for item in spec["indicators"]] == [
        ("ema20", 20),
        ("ema50", 50),
    ]


def test_an_ambiguous_indicator_name_is_refused():
    """Two EMA declarations make the bare name `EMA` unusable in a condition."""

    payload = _base_draft()
    payload["indicators"] = [
        {"name": "EMA", "origin": "EXPLICIT", "parameters": {"period": 20, "input": "close"}},
        {"name": "EMA", "origin": "EXPLICIT", "parameters": {"period": 50, "input": "close"}},
    ]
    payload["rules"][0]["parameters"] = {
        "left": "EMA",
        "operator": "crosses_above",
        "right": "ema50",
        "side": "long",
    }

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert "rule_conflict" in _codes(result)
    assert any(
        "declared more than once" in item["detail"]
        for item in result.report["rejections"]
        if item["code"] == "rule_conflict"
    )


def test_an_unmapped_indicator_name_is_refused():
    """§10.5: no fuzzy matching between a phrase and a registry type."""

    payload = _base_draft()
    payload["rules"][0]["parameters"].update({"left": "relative strength index", "right": 30})

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert "indicator_unmapped" in _codes(result)
    assert any(
        "neither a known column nor a declared indicator" in item["detail"]
        for item in result.report["rejections"]
        if item["code"] == "indicator_unmapped"
    )


# --------------------------------------------------- T19 validator stays a gate


def test_validator_rejection_is_never_downgraded_to_a_warning():
    """T19: an order type that needs an offset cannot become a 200 with a warning."""

    payload = _base_draft()
    payload["rules"][4]["parameters"] = {
        "fee_bps": 10,
        "slippage_bps": 5,
        "entry_order_type": "limit",
    }

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result != "COMPILED"
    assert result.spec is None
    assert "validation_failed" in _codes(result)
    errors = [issue for issue in result.report["dsl_validation"] if issue["severity"] == "error"]
    assert any(issue["code"] == "order_needs_offset" for issue in errors)
    assert result.report["warnings"] == []


def test_an_unknown_fill_model_is_engine_incompatible():
    """§13.2: the engine only fills at the next bar's open, so nothing else may be written."""

    payload = _base_draft()
    payload["rules"][4]["parameters"]["fill_model"] = "close_bar"

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert "engine_incompatible" in _codes(result)
    assert result.spec is None


def test_a_timeframe_outside_the_annualisation_table_is_engine_incompatible():
    """§13.2: `BARRS_PER_YEAR` is the whole vocabulary of runnable timeframes."""

    payload = _base_draft()
    payload["market"]["timeframes"] = ["3d"]

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert "engine_incompatible" in _codes(result)
    assert "market.timeframes" in [item["slot"] for item in result.report["rejections"]]


def test_the_extra_timeframes_are_reported_not_dropped():
    """A1 #3: only `timeframes[0]` is compiled, and the others are recorded."""

    payload = _base_draft()
    payload["market"]["timeframes"] = ["1d", "1h"]

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "COMPILED", _rejection_trace(result)
    slot = next(item for item in result.report["slots"] if item["slot"] == "market.timeframes")
    assert slot["value"] == ["1d"]
    assert slot["ignored"] == ["1h"]
    assert result.as_dsl()["market"]["timeframes"] == ["1d"]


def test_a_validator_warning_stays_a_warning():
    """§16.5: `cross_column_comparison` is reported as-is, neither dropped nor escalated."""

    payload = _base_draft()
    payload["rules"][0]["parameters"]["right"] = "rsi14"

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "COMPILED", _rejection_trace(result)
    warnings = result.report["warnings"]
    assert [warning["code"] for warning in warnings] == ["cross_column_comparison"]
    assert warnings[0]["severity"] == "warning"
    assert "validation_failed" not in _codes(result)


def test_the_compiler_reads_capability_the_way_it_is_stored():
    """§13.3 + ADR-170: the stored shape is `CapabilityDecision.as_dict()`.

    The report is built by the producer's own constructor, so a future rewrite of
    `capability_report_json` that goes back to the registry's `status`/`UNSUPPORTED`
    vocabulary turns this red instead of silently making the branch unreachable.
    """

    payload = _base_draft()
    capability = CapabilityDecision(
        verdict="PARTIALLY_SUPPORTED",
        requested=("short_selling",),
        missing=("short_selling",),
        reasons={"short_selling": "paper trading is long only"},
    ).as_dict()

    assert "status" not in capability
    assert capability["verdict"] == "PARTIALLY_SUPPORTED"

    result = compile_strategy_draft(payload, 3, "1.0.0", capability_report=capability)

    assert result.result == "REJECTED"
    assert "capability_missing" in _codes(result)
    assert result.report["capability"] == capability


def test_a_supported_capability_report_is_not_a_rejection():
    """§13.3: a real `SUPPORTED` report with empty lists must not cost a slot."""

    payload = _base_draft()
    capability = CapabilityDecision(verdict="SUPPORTED", requested=("indicators",)).as_dict()

    result = compile_strategy_draft(payload, 3, "1.0.0", capability_report=capability)

    assert result.result == "COMPILED", _rejection_trace(result)
    assert "capability_missing" not in _codes(result)


def test_an_unsupported_verdict_is_a_rejection_even_with_empty_lists():
    """§13.3: the verdict alone is enough — defence in depth, not the normal path."""

    payload = _base_draft()
    capability = CapabilityDecision(verdict="NEEDS_CAPABILITY").as_dict()

    result = compile_strategy_draft(payload, 3, "1.0.0", capability_report=capability)

    assert result.result == "REJECTED"
    assert "capability_missing" in _codes(result)
    assert "NEEDS_CAPABILITY" in json.dumps(result.report, ensure_ascii=False)


def test_a_short_rule_without_allow_short_is_asked_not_assumed():
    """§12: `market.allow_short` defaults to false in the DSL, and that is a decision."""

    payload = _base_draft()
    payload["rules"][0]["parameters"]["side"] = "short"

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert "market.allow_short" in _user_required_slots(result)


# -------------------------------------------------------------------- T20 hash


def test_compilation_is_deterministic():
    """T20: same input, same compiler version, identical output (docs/29 §15.4)."""

    first = compile_strategy_draft(_base_draft(), 3, "1.0.0")
    second = compile_strategy_draft(_base_draft(), 3, "1.0.0")

    assert first.result == second.result == "COMPILED", _rejection_trace(first)
    assert first.draft_hash == second.draft_hash
    assert first.compile_hash == second.compile_hash
    assert first.as_dsl() == second.as_dsl()
    assert _frozen(first.report) == _frozen(second.report)


def test_the_compile_hash_covers_the_target_but_the_draft_hash_does_not():
    """T20: `draft_hash` names the draft, `compile_hash` names draft + target."""

    first = compile_strategy_draft(_base_draft(), 3, "1.0.0")
    other = compile_strategy_draft(_base_draft(), 4, "1.0.0")

    assert first.draft_hash == other.draft_hash
    assert first.compile_hash != other.compile_hash
    assert first.compile_hash is not None and len(first.compile_hash) == 64


def test_the_compile_hash_is_not_the_immutable_hash():
    """§15.4: the two hashes answer different questions and must not be interchanged."""

    from app.data.strategy_service import immutable_hash

    result = compile_strategy_draft(_base_draft(), 3, "1.0.0")
    spec = result.as_dsl()

    assert result.compile_hash != immutable_hash(spec, spec["strategy"]["version"])


# -------------------------------------------------------- T21 no side effects


def test_the_compiler_takes_no_session_and_opens_no_socket(monkeypatch):
    """T21: the entry point has no database handle, and nothing reaches the network."""

    parameters = inspect.signature(compile_strategy_draft).parameters
    assert set(parameters) == {"draft", "strategy_id", "version", "capability_report"}

    from app.compiler import compiler as compiler_module

    for forbidden in (
        "Session",
        "sessionmaker",
        "create_strategy_version",
        "StrategyVersion",
        "BacktestRun",
        "AITask",
        "run_backtest",
    ):
        assert not hasattr(compiler_module, forbidden)

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the compiler must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", explode)
    assert compile_strategy_draft(_base_draft(), 3, "1.0.0").result == "COMPILED"


def test_a_persisted_row_is_read_without_a_database():
    """The row path only reads attributes; it never asks a session for anything."""

    class Row:
        id = 11
        run_id = 12
        hypothesis_id = 13
        draft_json = _base_draft()
        capability_report_json = None

    result = compile_strategy_draft(Row(), 3, "1.0.0")

    assert result.result == "COMPILED", _rejection_trace(result)
    assert result.report["draft"]["draft_id"] == 11
    assert result.report["draft"]["hypothesis_id"] == 13
    assert result.report["draft"]["run_id"] == 12


def test_an_unreadable_payload_is_rejected_not_raised():
    """A draft the compiler cannot even read is a refusal with a code, never an exception."""

    for payload in ({}, {"rules": "nonsense"}, {"strategy_name": "", "market": {}, "rules": []}):
        result = compile_strategy_draft(payload, 3, "1.0.0")
        assert result.result == "REJECTED"
        assert result.spec is None
        assert "parameter_invalid" in _codes(result)


def test_the_compiler_does_not_import_the_ai_layer_or_the_research_layer():
    """§13.4/§19: no research, no model, no prompt, no budget, no queue, no broker."""

    from app.compiler import compiler as compiler_module

    source = inspect.getsource(compiler_module)
    for forbidden in ("app.ai", "openai", "anthropic", "httpx", "requests", "celery", "redis"):
        assert forbidden not in source


def test_the_known_column_vocabulary_comes_from_the_validator():
    """§14.2: the compiler may not keep a second column list of its own."""

    from app.compiler import compiler as compiler_module

    source = inspect.getsource(compiler_module)
    assert "KNOWN_DERIVED" in source
    assert "close_position" in KNOWN_DERIVED


# ------------------------------------------- T22 columns the engine cannot read


def test_the_engine_column_guard_reads_the_engine_catalogue():
    """§13.2: the drift set is *computed* from the engine's own listing, never copied."""

    from app.compiler import mapping as mapping_module

    assert frozenset(spec.name for spec in FEATURE_CATALOGUE) == ENGINE_FEATURE_COLUMNS
    assert (
        frozenset(KNOWN_DERIVED - ENGINE_FEATURE_COLUMNS - set(BASE_COLUMNS))
        == DRIFTED_DERIVED_COLUMNS
    )
    assert set(DRIFTED_COLUMNS) == DRIFTED_DERIVED_COLUMNS

    source = inspect.getsource(mapping_module)
    for name in DRIFTED_COLUMNS:
        # A hand-written list would leave the literal in the source; the guard may not.
        assert f'"{name}"' not in source, f"mapping.py hard-codes the drifted column {name}"


@pytest.mark.parametrize("column", DRIFTED_COLUMNS)
def test_validator_accepts_but_the_engine_cannot_build_the_column(column: str):
    """T22: `validator_accepts_but_engine_does_not`.

    Half one proves the gap is real: the validator accepts a specification that reads this
    column, patched into a specification the compiler itself produced so the rest of it is
    known-good. Half two proves the compiler refuses the same condition in a draft —
    `build_features` never materialises the column, so the engine would read something
    that does not exist and the strategy would not do what its file says. `spec` and
    `compile_hash` stay `None`, so nothing unusable can be persisted.
    """

    clean = compile_strategy_draft(_base_draft(), 3, "1.0.0")
    assert clean.result == "COMPILED", _rejection_trace(clean)
    spec = json.loads(_frozen(clean.as_dsl()))
    node = spec["entry"]["long"]["all"][0]
    node["op"], node["left"], node["right"] = "gt", "close", column

    validator_report = validate_strategy(parse_spec(spec))
    assert validator_report.errors == [], [issue.as_dict() for issue in validator_report.errors]

    payload = _base_draft()
    # The user's own entry rule keeps the group populated, so the only code this draft
    # earns is the engine-compatibility one (a rejected-only group would also ask the
    # user for an entry rule, and the three-state rule would then say NEEDS_USER_DECISION).
    payload["rules"][0]["parameters"]["combine"] = "all"
    payload["rules"].append(
        {
            "id": "e-drifted",
            "field": "entry",
            "statement": f"and only when the close is above {column}",
            "origin": "EXPLICIT",
            "parameters": {
                "left": "close",
                "operator": "gt",
                "right": column,
                "side": "long",
                "combine": "all",
            },
        }
    )

    result = compile_strategy_draft(payload, 3, "1.0.0")

    assert result.result == "REJECTED", _rejection_trace(result)
    assert _codes(result) == ["engine_incompatible"]
    assert result.spec is None
    assert result.as_dsl() is None
    assert result.compile_hash is None
    trace = _rejection_trace(result)
    assert [(code, slot) for code, slot, _ in trace] == [("engine_incompatible", "entry.long")]
    assert column in trace[0][2]


# --------------------------------------------------------- examples regression

EXAMPLES = ("ema-cross-trend.json", "pa-breakout.json")

RISK_KEYS = (
    "stop_loss_atr_multiple",
    "take_profit_r_multiple",
    "take_profit_atr_multiple",
    "max_position_pct",
)


def _load_example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


def _generated_id(indicator: dict[str, Any], parameters: dict[str, Any]) -> str:
    period = indicator.get("period")
    if period is None and indicator.get("period_ref"):
        period = parameters.get(indicator["period_ref"])
    return f"{indicator['type'].lower()}{period}"


def _operand(token: Any, rename: dict[str, str]) -> Any:
    """§10.3: an operand is an indicator id, a column name, or a JSON number."""

    if isinstance(token, str):
        if token in rename:
            return rename[token]
        try:
            return float(token)
        except ValueError:
            return token
    return token


def _spec_operand(token: Any, rename: dict[str, str]) -> str:
    """The spec spells every operand as text, thresholds included (§10.3, §10.4)."""

    value = _operand(token, rename)
    if isinstance(value, str):
        return value
    return format(float(value), ".10g")


def _flat_risk(risk: dict[str, Any]) -> dict[str, Any]:
    """The example writes risk as the nested convenience form; the draft may not (§11.2)."""

    flat: dict[str, Any] = {}
    stop = risk.get("stop_loss") or {}
    if stop.get("type") == "atr_multiple":
        flat["stop_loss_atr_multiple"] = stop["multiple"]
    target = risk.get("take_profit") or {}
    if target.get("type") == "risk_multiple":
        flat["take_profit_r_multiple"] = target["multiple"]
    if target.get("type") == "atr_multiple":
        flat["take_profit_atr_multiple"] = target["multiple"]
    if risk.get("max_position_pct") is not None:
        flat["max_position_pct"] = risk["max_position_pct"]
    return flat


def _draft_from_example(example: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Express a legal example strategy as a draft with every decision written down.

    The example leans on the DSL's own `sizing` default; a draft may not, so the sizing
    rule states the size the engine would have resolved (`fraction is None` deploys
    `max_position_pct`, `app/research/engine.py:112-114`).
    """

    parameters = dict(example.get("parameters") or {})
    rename: dict[str, str] = {}
    indicators: list[dict[str, Any]] = []
    for indicator in example["indicators"]:
        rename[indicator["id"]] = _generated_id(indicator, parameters)
        declared: dict[str, Any] = {}
        if indicator.get("period") is not None:
            declared["period"] = indicator["period"]
        if indicator.get("period_ref"):
            declared["period_ref"] = indicator["period_ref"]
        if indicator.get("input"):
            declared["input"] = indicator["input"]
        indicators.append({"name": indicator["type"], "origin": "EXPLICIT", "parameters": declared})

    rules: list[dict[str, Any]] = []
    for field_name in ("entry", "exit"):
        for side, group in (example.get(field_name) or {}).items():
            for combine, nodes in group.items():
                if not nodes:
                    continue
                for position, node in enumerate(nodes):
                    rules.append(
                        {
                            "id": f"{field_name[0]}-{side}-{combine}-{position}",
                            "field": field_name,
                            "statement": f"{field_name} {side} {combine} #{position}",
                            "origin": "EXPLICIT",
                            "parameters": {
                                "left": _operand(node["left"], rename),
                                "operator": node["op"],
                                "right": _operand(node["right"], rename),
                                "side": side,
                                "combine": combine,
                            },
                        }
                    )

    rules.append(
        {
            "id": "k-risk",
            "field": "risk",
            "statement": "the example's stop, target and position cap, flattened",
            "origin": "EXPLICIT",
            "parameters": _flat_risk(example["risk"]),
        }
    )
    rules.append(
        {
            "id": "s-sizing",
            "field": "sizing",
            "statement": "the engine's own reading of the example: deploy the position cap",
            "origin": "EXPLICIT",
            "parameters": {
                "mode": "fixed_fraction",
                "fraction": example["risk"]["max_position_pct"],
            },
        }
    )
    rules.append(
        {
            "id": "c-costs",
            "field": "execution",
            "statement": "the example's fee and slippage",
            "origin": "EXPLICIT",
            "parameters": {
                "fee_bps": example["execution"]["fee_bps"],
                "slippage_bps": example["execution"]["slippage_bps"],
            },
        }
    )

    draft = {
        "strategy_name": example["strategy"]["name"],
        "status": "SUPPORTED",
        "market": {
            "markets": [],
            "asset_classes": list(example["market"]["asset_classes"]),
            "timeframes": list(example["market"]["timeframes"]),
            "universe": None,
        },
        "rules": rules,
        "indicators": indicators,
        "unknowns": [],
        "required_capabilities": [],
        "experimental_alternatives": [],
        "assumptions": [],
        "parameters": parameters,
        "notes": [],
        "understanding_of_original": example["strategy"]["description"],
    }
    return draft, rename


@pytest.mark.parametrize("name", EXAMPLES)
def test_a_legal_example_survives_the_compiler(name: str):
    """§十九: an example strategy compiles with the same DSL semantics it already has."""

    example = _load_example(name)
    draft, rename = _draft_from_example(example)
    result = compile_strategy_draft(draft, 11, "2.0.0")

    assert result.result == "COMPILED", _rejection_trace(result)
    spec = result.as_dsl()
    parameters = dict(example.get("parameters") or {})

    # indicators: the same types and periods, under the §10.5 ids
    assert spec["indicators"] == [
        {
            "id": _generated_id(indicator, parameters),
            "type": indicator["type"],
            "period": indicator.get("period"),
            "period_ref": indicator.get("period_ref"),
            "input": indicator.get("input", "close"),
            "params": {},
        }
        for indicator in example["indicators"]
    ]
    assert spec["parameters"] == parameters

    # entry/exit: same operators and bounds, same order, same group kind
    for field_name in ("entry", "exit"):
        for side, group in (example.get(field_name) or {}).items():
            expected_key = "all" if group.get("all") else "any"
            expected = [
                {
                    "op": node["op"],
                    "left": _spec_operand(node["left"], rename),
                    "right": _spec_operand(node["right"], rename),
                }
                for node in group[expected_key]
            ]
            got = spec[field_name][side]
            assert _conditions(got[expected_key]) == expected
            assert got["any" if expected_key == "all" else "all"] is None

    # risk and execution carry the example's numbers through unchanged
    flat = _flat_risk(example["risk"])
    for key in RISK_KEYS:
        assert spec["risk"][key] == flat.get(key)
    assert spec["execution"]["fill_model"] == "next_bar_open"
    assert spec["execution"]["entry_order_type"] == "market"
    assert spec["execution"]["order_valid_bars"] == 1
    assert spec["execution"]["fee_bps"] == example["execution"]["fee_bps"]
    assert spec["execution"]["slippage_bps"] == example["execution"]["slippage_bps"]
    assert spec["execution"]["initial_capital"] == 10000.0  # COMPILER_RULE, docs/29 §12
    assert spec["execution"]["sizing"]["mode"] == "fixed_fraction"
    assert spec["execution"]["sizing"]["fraction"] == example["risk"]["max_position_pct"]

    # §14.3: features are never written, and the compiler signs the spec it produced
    assert example.get("features"), "the example is expected to name features"
    assert spec["features"] == []
    assert spec["strategy"]["source"]["type"] == "compiler"
    assert spec["strategy"]["source"]["compiler_version"] == result.compiler_version
    assert spec["strategy"]["description"] is None
    assert spec["market"]["asset_classes"] == example["market"]["asset_classes"]
    assert spec["market"]["timeframes"] == example["market"]["timeframes"]
    assert spec["market"]["allow_short"] is False
    assert "symbol" not in _frozen(spec)


@pytest.mark.parametrize("name", EXAMPLES)
def test_an_example_without_a_sizing_decision_is_refused(name: str):
    """The example leans on the DSL default; a draft that does the same must be asked."""

    example = _load_example(name)
    draft, _ = _draft_from_example(example)
    draft["rules"] = [rule for rule in draft["rules"] if rule["field"] != "sizing"]

    result = compile_strategy_draft(draft, 11, "2.0.0")

    assert result.result == "NEEDS_USER_DECISION"
    assert result.spec is None
    assert "sizing.mode" in _user_required_slots(result)
