"""The evidence-quote contract: the prompt, the role contracts, the schemas and the
validator all say the same thing.

ADR-159 makes a quote a verbatim, *contiguous* passage of the named source, and
``_quote_span`` has always enforced that. A real run showed the surfaces the model
reads did not state it: the verbatim rule read as EXPLICIT-only (while
``_check_evidence`` checks every non-empty quote whatever its origin), and nothing said
that citing two places needs two evidence entries instead of one quote stitched across
the gap. These tests pin the model-facing wording to what the validator does, and pin
the three ways a refused quote is explained — without loosening any of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ai import research as research_module
from app.ai import research_schemas as gates

CONTRACT_DIR = Path(research_module.__file__).resolve().parent / "contracts"
SOURCE_REF = "aapl-daily"
HEADER = "symbol=AAPL asset_id=3 exchange=NASDAQ asset_class=stock currency=USD"
BAR_A = "08/12/2026 08:00:00,305.1000061,305.66000366,300.57000732,302.25,41657800"
#: Filler bars: they keep BAR_A and BAR_B apart, so the two really are separate
#: passages of the source (in the run that exposed this they were 28 lines apart).
BAR_MID = "09/18/2026 08:00:00,325.0,327.0,320.0,321.5,86588200"
BAR_B = "09/22/2026 08:00:00,340.14001465,345.33999634,338.75,339.75,40711800"
SOURCE = (
    f"{HEADER}\ncolumns: timestamp,open,high,low,close,volume\n"
    f"{BAR_A}\n{BAR_MID}\n{BAR_MID}\n{BAR_B}\n"
)
#: Both passages really are in the source; the bars between them are not quoted.
STITCHED = f"{BAR_A}\n{BAR_B}"
#: A sentence of the model's own making: the numbers are real, the sentence is not.
SUMMARY = "close prices rising from 302.25 to 339.75 then pulling back"
SOURCE_HASH = "0" * 64

#: Every model-facing surface has to carry these words, so prompt, role contracts,
#: schema description and validator cannot drift apart again.
CONTRACT_PHRASES = (
    "one contiguous passage",
    "copied character for character",
    "only the amount of whitespace may differ",
    "one evidence entry per passage",
)
#: ... and the rule applies to every origin, not only to EXPLICIT.
ORIGIN_PHRASE = "whatever the rule's origin"


def _evidence(*quotes: str | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for quote in quotes:
        entry: dict[str, Any] = {"source_ref": SOURCE_REF, "locator": "bar"}
        if quote is not None:
            entry["quote"] = quote
        entries.append(entry)
    return entries


def _payload(
    *,
    rule_id: str,
    origin: str,
    field: str,
    evidence: list[dict[str, Any]],
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "strategy_name": "quoted",
        "understanding": "the material describes a rule",
        "rules": [
            {
                "id": rule_id,
                "field": field,
                "statement": "the rule the material states",
                "origin": origin,
                "confidence": "low",
                "evidence": evidence,
            }
        ],
        "ambiguities": [],
        "unknowns": [],
        "capability_requests": [],
        "assumptions": assumptions or [],
    }


def _explicit(field: str, *quotes: str | None) -> dict[str, Any]:
    return _payload(
        rule_id="r-explicit", origin="EXPLICIT", field=field, evidence=_evidence(*quotes)
    )


def _assumed(field: str, *quotes: str | None) -> dict[str, Any]:
    return _payload(
        rule_id="r-assumed",
        origin="ASSUMED",
        field=field,
        evidence=_evidence(*quotes),
        assumptions=[
            {"statement": "the threshold is assumed", "applies_to": [field], "reason": "test"}
        ],
    )


def _run(payload: dict[str, Any]) -> tuple[gates.StrategyHypothesis, list[gates.Violation]]:
    hypothesis = gates.parse_hypothesis(payload)
    violations = gates.validate_hypothesis(
        hypothesis, sources={SOURCE_REF: SOURCE}, source_hashes={SOURCE_REF: SOURCE_HASH}
    )
    return hypothesis, violations


def _flat(text: str) -> str:
    return " ".join(text.split())


def _only(violations: list[gates.Violation]) -> str:
    assert [violation.code for violation in violations] == ["evidence_mismatch"]
    return violations[0].message


# --------------------------------------------------------------------------- #
# The validator's behaviour, quoted verbatim (unchanged by this contract)
# --------------------------------------------------------------------------- #
def test_one_contiguous_quote_is_accepted() -> None:
    hypothesis, violations = _run(_explicit("market", BAR_A))

    assert violations == []
    evidence = hypothesis.rules[0].evidence[0]
    assert evidence.verified is True
    assert SOURCE[evidence.char_start : evidence.char_end] == BAR_A
    assert evidence.verified_against == SOURCE_HASH


def test_whitespace_alone_may_differ() -> None:
    reflowed = HEADER.replace(" ", "\n")

    hypothesis, violations = _run(_explicit("market", reflowed))

    assert violations == []
    evidence = hypothesis.rules[0].evidence[0]
    # The span points at the source's own characters, not at the model's reflowed ones.
    assert SOURCE[evidence.char_start : evidence.char_end] == HEADER


def test_two_real_passages_joined_into_one_quote_are_refused() -> None:
    _hypothesis, violations = _run(_explicit("market", STITCHED))

    message = _only(violations)
    assert "not one contiguous passage" in message
    assert "one evidence entry per passage" in message
    assert "does not appear" not in message


def test_a_summary_in_the_model_s_own_words_is_refused() -> None:
    _hypothesis, violations = _run(_assumed("indicator", SUMMARY))

    message = _only(violations)
    assert "does not appear" in message
    assert "summary" in message
    assert "one evidence entry per passage" not in message


def test_two_evidence_entries_for_two_passages_both_pass() -> None:
    hypothesis, violations = _run(_explicit("market", BAR_A, BAR_B))

    assert violations == []
    evidence = hypothesis.rules[0].evidence
    assert [item.verified for item in evidence] == [True, True]
    assert SOURCE[evidence[0].char_start : evidence[0].char_end] == BAR_A
    assert SOURCE[evidence[1].char_start : evidence[1].char_end] == BAR_B


def test_an_assumed_rule_may_carry_no_quote() -> None:
    _hypothesis, violations = _run(_assumed("indicator", None))

    assert violations == []


def test_an_assumed_rule_with_a_real_quote_passes() -> None:
    hypothesis, violations = _run(_assumed("indicator", BAR_A))

    assert violations == []
    assert hypothesis.rules[0].evidence[0].verified is True


def test_an_assumed_rule_with_a_made_up_quote_is_refused() -> None:
    # The check is origin-independent: only the *requirement* is EXPLICIT's.
    _hypothesis, violations = _run(_assumed("indicator", SUMMARY))

    assert _only(violations)


@pytest.mark.parametrize(
    "quote",
    [
        BAR_A.replace(",", ";"),  # punctuation changed
        HEADER.swapcase(),  # case changed
        BAR_A.replace("305.1000061", "305.1"),  # number re-formatted
        f"{BAR_B} {BAR_A}",  # the same two bars, order swapped
    ],
)
def test_no_other_normalisation_is_allowed(quote: str) -> None:
    _hypothesis, violations = _run(_explicit("market", quote))

    assert _only(violations)


def test_a_refusal_says_which_way_the_quote_fails() -> None:
    stitched = _only(_run(_explicit("market", STITCHED))[1])
    partial = _only(_run(_explicit("market", f"{BAR_A}\nclose prices rising"))[1])
    absent = _only(_run(_explicit("market", SUMMARY))[1])

    assert "not one contiguous passage" in stitched
    assert "one evidence entry per passage" in stitched
    assert "only part appears" in partial
    assert "does not appear" in absent
    assert len({stitched, partial, absent}) == 3


# --------------------------------------------------------------------------- #
# The surfaces the model reads
# --------------------------------------------------------------------------- #
def _model_messages() -> list[str]:
    hypothesis = gates.parse_hypothesis(_explicit("market", BAR_A))
    return [
        research_module._researcher_prompt("what is the rule?", {SOURCE_REF: SOURCE}, []),
        research_module._architect_prompt(
            "what is the rule?", hypothesis, {SOURCE_REF: SOURCE}, []
        ),
    ]


def test_both_prompts_the_model_receives_carry_the_quote_contract() -> None:
    for message in _model_messages():
        flat = _flat(message)
        for phrase in CONTRACT_PHRASES:
            assert phrase in flat
        assert ORIGIN_PHRASE in flat


def test_the_researcher_contract_states_the_quote_contract() -> None:
    flat = _flat((CONTRACT_DIR / "RESEARCHER.md").read_text(encoding="utf-8"))

    for phrase in CONTRACT_PHRASES:
        assert phrase in flat
    assert ORIGIN_PHRASE in flat


def test_the_architect_contract_states_the_quote_contract() -> None:
    flat = _flat((CONTRACT_DIR / "STRATEGY_ARCHITECT.md").read_text(encoding="utf-8"))

    for phrase in CONTRACT_PHRASES:
        assert phrase in flat
    assert ORIGIN_PHRASE in flat


def _quote_properties(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "quote" and isinstance(value, dict):
                found.append(value)
            found.extend(_quote_properties(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_quote_properties(item))
    return found


def test_both_model_facing_schemas_carry_the_same_quote_description() -> None:
    researcher = _quote_properties(gates.RESEARCH_SCHEMA)
    architect = _quote_properties(gates.FORMALIZATION_SCHEMA)

    assert len(researcher) == 1  # hypothesis rules
    assert len(architect) == 2  # draft indicators and draft rules
    for property_schema in researcher + architect:
        assert property_schema["description"] == gates.EVIDENCE_QUOTE_DESCRIPTION

    flat = _flat(gates.EVIDENCE_QUOTE_DESCRIPTION)
    for phrase in CONTRACT_PHRASES:
        assert phrase in flat
