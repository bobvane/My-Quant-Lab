"""The ``assumptions[].applies_to`` contract: schema, prompt and gate must agree.

The v2.4.2 real research run was refused with seven ``assumed_not_disclosed``
violations because the model wrote rule ids (``["R4_sma20"]``) into
``assumptions[].applies_to`` — which is what both role contracts asked for — while
``_disclosure_violations`` matches ``rule.field``. The fix states one vocabulary
(``RULE_FIELDS``) everywhere the model reads it: the JSON schema the runtime sends,
both task prompts and both role contracts. The gate itself is unchanged, so a rule id
still discloses nothing.
"""

from __future__ import annotations

import hashlib

import pytest
from research_payloads import (
    MARTIN,
    NOTE,
    QUESTION,
    RecordingProvider,
    draft_payload,
    hypothesis_payload,
    make_provider,
    note_inputs,
    recorded_messages,
    run_research,
    run_research_through_the_router,
    variant,
)

from app.ai import research as service
from app.ai import research_schemas as gates
from app.ai.role_contracts import contract_for_role, task_output_schemas
from app.domain.models import AIProvider


@pytest.fixture
def provider(db_session) -> AIProvider:
    return make_provider(db_session)


def _applies_to(schema: dict) -> dict:
    return schema["properties"]["assumptions"]["items"]["properties"]["applies_to"]


def _sources() -> dict[str, str]:
    return {MARTIN: NOTE}


def _source_hashes() -> dict[str, str]:
    return {MARTIN: hashlib.sha256(NOTE.encode("utf-8")).hexdigest()}


def _disclosures(violations: list) -> list:
    return [violation for violation in violations if violation.code == "assumed_not_disclosed"]


# ------------------------------------------------------------------- the schema


@pytest.mark.parametrize(
    "task_type",
    ["strategy_research", "strategy_formalization"],
    ids=["researcher", "architect"],
)
def test_the_schema_the_model_gets_declares_the_rule_field_vocabulary(task_type):
    applies_to = _applies_to(task_output_schemas()[task_type])

    assert applies_to["items"]["enum"] == list(gates.RULE_FIELDS)
    assert applies_to["description"] == gates.APPLIES_TO_DESCRIPTION
    assert "never the rule id" in applies_to["description"]


def test_the_two_schemas_word_the_contract_once():
    research = _applies_to(task_output_schemas()["strategy_research"])["description"]
    formalization = _applies_to(task_output_schemas()["strategy_formalization"])["description"]

    assert research == formalization
    assert "rules[].field" in research


def test_a_rule_id_is_not_part_of_the_vocabulary():
    assert not set(gates.RULE_FIELDS) & {"r-oversold", "r-rebound", "R4_sma20", "R6_entry"}


# ------------------------------------------------------------------ the prompts


@pytest.mark.parametrize(
    ("role", "task_type"),
    [
        ("RESEARCHER", "strategy_research"),
        ("STRATEGY_ARCHITECT", "strategy_formalization"),
    ],
)
def test_each_role_contract_names_the_field_and_never_the_rule_id(role, task_type):
    text = contract_for_role(role).task_prompts[task_type]

    assert "applies_to" in text
    for field in gates.RULE_FIELDS:
        assert field in text
    assert "never the rule id" in text
    assert "the rule ids" not in text


def test_both_prompts_the_model_receives_teach_the_same_contract(db_session, provider, monkeypatch):
    run = run_research_through_the_router(
        db_session,
        provider,
        [hypothesis_payload(), draft_payload()],
        monkeypatch,
        question=variant(QUESTION, "applies_to 契约"),
    )

    assert run.status == "completed"
    messages = recorded_messages()
    assert len(messages) == 2
    for call in messages:
        system = " ".join(message["content"] for message in call if message["role"] == "system")
        user = " ".join(message["content"] for message in call if message["role"] == "user")
        assert "never the rule id" in system
        assert "applies_to" in user
        assert "never the rule id" in user
        for field in gates.RULE_FIELDS:
            assert field in user


def test_the_runtime_sends_the_vocabulary_schema_to_the_provider(db_session, provider, monkeypatch):
    from app.ai import runtime as runtime_module

    sent: list[dict | None] = []

    class SchemaRecordingProvider(RecordingProvider):
        def structured_output(self, messages, *, model="", schema=None):
            sent.append(schema)
            return super().structured_output(messages, model=model, schema=schema)

    RecordingProvider.queue = [hypothesis_payload(), draft_payload()]
    RecordingProvider.instances = []
    monkeypatch.setattr(runtime_module, "OpenAICompatibleProvider", SchemaRecordingProvider)

    run = service.start_research(
        db_session,
        question=variant(QUESTION, "schema 契约"),
        inputs=note_inputs(),
        providers=[(provider, "sk-test", [])],
        router_factory=None,
    )
    db_session.commit()

    assert run.status == "completed"
    assert len(sent) == 2
    assert sent[0] is gates.RESEARCH_SCHEMA
    assert sent[1] is gates.FORMALIZATION_SCHEMA


# -------------------------------------------------------------------- the gate


def test_a_field_name_discloses_an_assumed_rule():
    hypothesis = gates.parse_hypothesis(hypothesis_payload())

    violations = gates.validate_hypothesis(
        hypothesis, sources=_sources(), source_hashes=_source_hashes()
    )

    assert _disclosures(violations) == []


def test_a_rule_id_discloses_nothing():
    payload = hypothesis_payload()
    payload["assumptions"][0]["applies_to"] = ["r-oversold"]
    payload["assumptions"][1]["applies_to"] = ["r-rebound"]

    violations = _disclosures(
        gates.validate_hypothesis(
            gates.parse_hypothesis(payload), sources=_sources(), source_hashes=_source_hashes()
        )
    )

    assert [violation.field_name for violation in violations] == ["r-oversold", "r-rebound"]
    assert all("never the rule id" in violation.message for violation in violations)


def test_a_rule_id_in_a_draft_assumption_discloses_nothing():
    hypothesis = gates.parse_hypothesis(hypothesis_payload())
    payload = draft_payload()
    payload["assumptions"][0]["applies_to"] = ["d-entry"]

    violations = _disclosures(
        gates.validate_draft(
            gates.parse_draft(payload),
            hypothesis=hypothesis,
            sources=_sources(),
            source_hashes=_source_hashes(),
        )
    )

    assert "d-entry" in [violation.field_name for violation in violations]


# ---------------------------------------------------------------- end to end


def test_a_run_that_discloses_by_rule_id_is_rejected(db_session, provider):
    payload = hypothesis_payload()
    payload["assumptions"][0]["applies_to"] = ["r-oversold"]
    payload["assumptions"][1]["applies_to"] = ["r-rebound"]

    run, _router = run_research(
        db_session, provider, [payload, payload], question=variant(QUESTION, "规则 id 披露")
    )

    assert run.status == "rejected"
    assert {violation["code"] for violation in run.violations_json} == {"assumed_not_disclosed"}


def test_a_run_that_discloses_by_field_still_completes(db_session, provider):
    run, _router = run_research(
        db_session,
        provider,
        [hypothesis_payload(), draft_payload()],
        question=variant(QUESTION, "字段披露"),
    )

    assert run.status == "completed"
    payload = service.run_payload(db_session, run)
    assert payload["hypothesis"]["content"]["assumptions"][0]["applies_to"] == ["indicator"]
    assert payload["draft"]["content"]["assumptions"][0]["applies_to"] == ["indicator"]
