"""The architect step and the capability gate (v1.9.8).

A hypothesis becomes a StrategyDraft only when it survives the same gate chain
a researcher answer does, plus the capability gate: the *server* decides
SUPPORTED / PARTIALLY_SUPPORTED / NEEDS_CAPABILITY from the registry, and a
draft that claims more than the registry supports is refused rather than stored.
The §8 case — a cross-sectional strategy may not quietly become a single-asset
one — is the reference case for that gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from research_payloads import (
    MARTIN,
    MOMENTUM,
    MOMENTUM_NOTE,
    MOMENTUM_QUESTION,
    QUESTION,
    draft_payload,
    hypothesis_payload,
    make_provider,
    momentum_draft_payload,
    momentum_hypothesis_payload,
    note_inputs,
    run_research,
    variant,
)
from sqlalchemy import func, select

from app.ai import research as service
from app.ai.research_schemas import (
    FORMALIZATION_SCHEMA,
    assess_draft_capabilities,
    parse_draft,
    parse_hypothesis,
)
from app.ai.role_contracts import contract_for_role, task_output_schemas
from app.api.routers.ai import NOT_CONFIGURED_DETAIL
from app.domain.models import (
    AIProvider,
    AIResearchRun,
    AITask,
    BacktestRun,
    StrategyVersion,
)
from app.domain.models import (
    StrategyDraft as StrategyDraftRow,
)


@pytest.fixture
def provider(db_session) -> AIProvider:
    return make_provider(db_session)


def codes(run) -> set[str]:
    return {violation["code"] for violation in run.violations_json}


def refused(db_session, provider, hypothesis, draft, tag: str) -> set[str]:
    """Run one hypothesis + a bad draft, and report why the draft was refused."""

    run, _ = run_research(
        db_session, provider, [hypothesis, draft, draft], question=variant(QUESTION, tag)
    )
    assert run.status == "rejected"
    return codes(run)


def stored_drafts(db_session) -> list[StrategyDraftRow]:
    return list(db_session.scalars(select(StrategyDraftRow)).all())


# ------------------------------------------------------------------- architect


def test_strategy_architect(db_session, provider):
    run, router = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    assert run.status == "completed"

    assert [call.task_type for call in router.calls] == [
        "strategy_research",
        "strategy_formalization",
    ]
    architect = router.calls[1]
    assert architect.role == "STRATEGY_ARCHITECT"
    assert architect.prompt_name == "strategy_formalization"
    assert architect.prompt_version == "1.1.0"
    assert architect.schema is FORMALIZATION_SCHEMA
    # The architect formalizes a hypothesis, so the call carries it, and the
    # capability registry is what it has to check the request against.
    assert '"r-entry"' in architect.user_prompt
    assert "capability registry" in architect.user_prompt.lower()
    assert architect.structured_facts["hypothesis_id"] is not None

    payload = service.run_payload(db_session, run)
    draft = payload["draft"]
    assert draft["run_id"] == run.id
    assert draft["hypothesis_id"] == payload["hypothesis"]["hypothesis_id"]
    assert draft["version"] == "1.0"
    assert draft["status"] == "SUPPORTED"
    assert draft["capability_status"] == "SUPPORTED"
    assert draft["model_status"] == "SUPPORTED"
    assert draft["executable"] is False
    assert draft["compiled_strategy_version_id"] is None
    assert draft["content"]["strategy_name"] == "BTC oversold rebound (RSI formalization)"
    assert payload["capability_status"] == "SUPPORTED"

    task = db_session.get(AITask, run.architect_task_id)
    assert task.role == "STRATEGY_ARCHITECT"
    assert task.task_type == "strategy_formalization"
    assert task.research_run_id == run.id
    assert task.strategy_version_id is None


def test_strategy_architect_contract():
    contract = contract_for_role("STRATEGY_ARCHITECT")
    assert contract.version == "1.1.0"
    assert "strategy_formalization" in contract.task_types
    assert task_output_schemas()["strategy_formalization"] is FORMALIZATION_SCHEMA
    assert "research.py" in contract.body
    assert "NEEDS_CAPABILITY" in contract.body
    assert "no code calls this contract" not in contract.body


# ----------------------------------------------------------------- draft shape


def test_strategy_draft_schema(db_session, provider):
    missing = draft_payload()
    missing.pop("market")
    run, _ = run_research(db_session, provider, [hypothesis_payload(), missing, missing])
    assert run.status == "rejected"
    assert "schema_invalid" in codes(run)

    # A draft is not a compiled artifact and never carries code.
    for key, value in (
        ("compiled_strategy_version_id", 1),
        ("python", "print('hello')"),
        ("strategy_spec", {"version": "1.0"}),
    ):
        extra = draft_payload()
        extra[key] = value
        found = refused(db_session, provider, hypothesis_payload(), extra, f"多了 {key}")
        assert "forbidden_content" in found

    assert stored_drafts(db_session) == []


def test_strategy_draft_provenance(db_session, provider):
    """A draft may weaken the research, never strengthen it, never drop it."""

    inflated = draft_payload()
    inflated["rules"][2]["origin"] = "EXPLICIT"
    inflated["rules"][2]["evidence"] = [{"source_ref": MARTIN}]
    assert "provenance_stronger_than_hypothesis" in refused(
        db_session, provider, hypothesis_payload(), inflated, "把假设升成原文规则"
    )

    invented = draft_payload()
    invented["rules"].append(
        {
            "id": "d-new",
            "field": "risk",
            "statement": "固定 2% 止损。",
            "origin": "INFERRED",
            "evidence": [{"source_ref": MARTIN}],
        }
    )
    assert "new_rule_must_be_assumed" in refused(
        db_session, provider, hypothesis_payload(), invented, "凭空多出一条规则"
    )

    orphan = draft_payload()
    orphan["rules"][1]["derived_from"] = "r-nope"
    assert "unknown_derivation" in refused(
        db_session, provider, hypothesis_payload(), orphan, "指向不存在的规则"
    )

    mismatched = draft_payload()
    mismatched["rules"][1]["field"] = "exit"
    found = refused(db_session, provider, hypothesis_payload(), mismatched, "派生的字段对不上")
    assert "derivation_field_mismatch" in found

    dropped = draft_payload()
    dropped["rules"] = [rule for rule in dropped["rules"] if rule["id"] != "d-intent"]
    found = refused(db_session, provider, hypothesis_payload(), dropped, "丢掉了用户明说的规则")
    assert "dropped_explicit_rule" in found

    # ... and what the research could not pin down may not disappear either.
    quiet = draft_payload()
    quiet["unknowns"] = [
        unknown for unknown in quiet["unknowns"] if unknown["field"] != "timeframe"
    ]
    found = refused(db_session, provider, hypothesis_payload(), quiet, "把没确定的事情咽下去")
    assert "dropped_unknown" in found


def test_strategy_draft_does_not_execute(db_session, provider):
    run, _ = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    assert run.status == "completed"
    draft = service.run_payload(db_session, run)["draft"]
    assert draft["executable"] is False
    assert draft["content"]["executable"] is False
    assert draft["compiled_strategy_version_id"] is None
    assert db_session.scalar(select(func.count()).select_from(StrategyVersion)) == 0
    assert db_session.scalar(select(func.count()).select_from(BacktestRun)) == 0

    executable = draft_payload()
    executable["executable"] = True
    assert "not_executable" in refused(
        db_session, provider, hypothesis_payload(), executable, "自称可以直接跑"
    )

    # The research layer has no path to the engine at all.
    source = Path(service.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "run_backtest",
        "BacktestEngine",
        "walk_forward",
        "monte_carlo",
        "run_sensitivity",
        "StrategyVersion(",
        "BacktestRun(",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------- capabilities


def test_supported_capability(db_session, provider):
    run, _ = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    assert run.status == "completed"

    report = service.run_payload(db_session, run)["draft"]["capability_report"]
    assert report["verdict"] == "SUPPORTED"
    assert report["missing"] == []
    assert report["partial"] == []
    assert "rsi" in {token.lower() for token in report["supported"]}
    assert all(not item["overclaimed"] for item in report["items"])


def test_partially_supported_capability(db_session, provider):
    hypothesis = hypothesis_payload()
    hypothesis["capability_requests"] = [
        {
            "capability": "short_selling",
            "statement": "策略需要做空。",
            "reason": "看跌时反手。",
        }
    ]
    draft = draft_payload()
    draft["status"] = "PARTIALLY_SUPPORTED"
    run, _ = run_research(db_session, provider, [hypothesis, draft])
    assert run.status == "completed"

    report = service.run_payload(db_session, run)["draft"]["capability_report"]
    assert report["verdict"] == "PARTIALLY_SUPPORTED"
    assert report["partial"] == ["short_selling"]
    assert report["missing"] == []
    assert run.capability_status == "PARTIALLY_SUPPORTED"
    item = next(row for row in report["items"] if row["capability"] == "short_selling")
    assert item["status"] == "PARTIALLY_SUPPORTED"
    assert item["reason"]


def test_needs_capability(db_session, provider):
    """Nothing the draft asks for exists, so there is nothing to build with."""

    run, _ = run_research(
        db_session,
        provider,
        [
            momentum_hypothesis_payload(),
            momentum_draft_payload(
                status="NEEDS_CAPABILITY", with_ema=False, with_alternative=True
            ),
        ],
        question=MOMENTUM_QUESTION,
        inputs=note_inputs(source_ref=MOMENTUM, text=MOMENTUM_NOTE),
    )
    assert run.status == "completed"

    report = service.run_payload(db_session, run)["draft"]["capability_report"]
    assert report["verdict"] == "NEEDS_CAPABILITY"
    assert set(report["missing"]) == {"cross_sectional_universe", "portfolio_rules"}
    assert report["supported"] == []
    assert run.capability_status == "NEEDS_CAPABILITY"
    item = next(row for row in report["items"] if row["capability"] == "cross_sectional_universe")
    assert item["status"] == "UNSUPPORTED"
    assert item["affected_rule"] == "d-signal"
    assert item["reason"]
    assert item["suggested_alternative"]
    assert item["alternative_is_experimental"] is True


def test_the_verdict_is_computed_on_the_server(db_session, provider):
    """The model may not talk the registry into supporting something."""

    hypothesis = parse_hypothesis(momentum_hypothesis_payload())
    overclaiming = parse_draft(momentum_draft_payload(status="SUPPORTED"))
    decision = assess_draft_capabilities(overclaiming, hypothesis)
    assert decision.verdict == "PARTIALLY_SUPPORTED"
    assert set(decision.missing) == {"cross_sectional_universe", "portfolio_rules"}
    assert "ema" in decision.supported
    assert list(decision.overclaims) == []

    # With nothing in the registry to build the signal out of, there is no
    # partial support left to claim.
    empty_handed = parse_draft(momentum_draft_payload(status="SUPPORTED", with_ema=False))
    assert assess_draft_capabilities(empty_handed, hypothesis).verdict == "NEEDS_CAPABILITY"

    unknown_indicator = draft_payload()
    unknown_indicator["indicators"] = [{"name": "VWAP", "origin": "ASSUMED", "parameters": {}}]
    assert "capability_overclaim" in refused(
        db_session, provider, hypothesis_payload(), unknown_indicator, "自称支持 VWAP"
    )


def test_no_silent_downgrade(db_session, provider):
    """A cross-sectional strategy may not quietly become a single-asset one."""

    silent = momentum_draft_payload(status="SUPPORTED")
    run, _ = run_research(
        db_session,
        provider,
        [momentum_hypothesis_payload(), silent, silent],
        question=MOMENTUM_QUESTION,
        inputs=note_inputs(source_ref=MOMENTUM, text=MOMENTUM_NOTE),
    )
    assert run.status == "rejected"
    assert "capability_overclaim" in codes(run)
    said = " ".join(violation["message"] for violation in run.violations_json)
    assert "cross_sectional_universe" in said
    assert stored_drafts(db_session) == []

    honest = momentum_draft_payload(status="PARTIALLY_SUPPORTED", with_alternative=True)
    run, _ = run_research(
        db_session,
        provider,
        [momentum_hypothesis_payload(), honest],
        question=variant(MOMENTUM_QUESTION, "诚实版"),
        inputs=note_inputs(source_ref=MOMENTUM, text=MOMENTUM_NOTE),
    )
    assert run.status == "completed"

    draft = service.run_payload(db_session, run)["draft"]
    assert draft["status"] == "PARTIALLY_SUPPORTED"
    assert draft["model_status"] == "PARTIALLY_SUPPORTED"
    assert draft["capability_status"] == "PARTIALLY_SUPPORTED"
    report = draft["capability_report"]
    assert report["verdict"] == "PARTIALLY_SUPPORTED"
    assert "ema" in report["supported"]
    assert set(report["missing"]) == {"cross_sectional_universe", "portfolio_rules"}
    content = draft["content"]
    assert content["understanding_of_original"]
    # The 20-day momentum leg is carried by a definition the AI chose, and the
    # draft has to admit that instead of presenting it as the user's rule.
    formalized = " ".join(item["statement"] for item in content["assumptions"])
    assert "20 日动量" in formalized
    alternatives = content["experimental_alternatives"]
    assert alternatives and alternatives[0]["differs_from_original"] is True
    assert alternatives[0]["what_it_gives_up"]
    asked = {need["capability"] for need in content["required_capabilities"]}
    assert {"cross_sectional_universe", "portfolio_rules"} <= asked


def test_an_alternative_that_is_not_marked_experimental_is_refused(db_session, provider):
    sneaky = momentum_draft_payload(status="NEEDS_CAPABILITY", with_alternative=True)
    sneaky["required_capabilities"][0]["alternative_is_experimental"] = False

    run, _ = run_research(
        db_session,
        provider,
        [momentum_hypothesis_payload(), sneaky, sneaky],
        question=MOMENTUM_QUESTION,
        inputs=note_inputs(source_ref=MOMENTUM, text=MOMENTUM_NOTE),
    )
    assert run.status == "rejected"
    assert "alternative_not_marked_experimental" in codes(run)


# ------------------------------------------------------------------ the API


def patch_pipeline(monkeypatch, outputs):
    """Answer the pipeline from a script so an endpoint test never dials out."""

    queue = list(outputs)
    calls = []

    def fake_run_task(db, request, **kwargs):
        calls.append(request)
        if not queue:
            raise AssertionError("the endpoint asked for more answers than the script holds")
        return {
            "explanation": queue.pop(0),
            "cached": False,
            "task_id": None,
            "model": "test-model",
            "cost_usd_estimated": 0.0,
        }

    monkeypatch.setattr(service, "run_task", fake_run_task)
    monkeypatch.setattr(
        service, "get_active_providers", lambda db: [("fake-provider", "sk-test", [])]
    )
    return calls


def research_body(**overrides) -> dict:
    body = {
        "question": QUESTION,
        "sources": [
            {"kind": "user_input", "text": MOMENTUM_NOTE, "source_ref": MARTIN, "label": "Martin"}
        ],
    }
    body.update(overrides)
    return body


def test_the_research_endpoints_walk_the_whole_pipeline(client, monkeypatch):
    patch_pipeline(monkeypatch, [hypothesis_payload(), draft_payload(), draft_payload()])

    created = client.post("/api/v1/ai/research", json=research_body())
    assert created.status_code == 200, created.text
    payload = created.json()
    run_id = payload["run_id"]
    assert payload["status"] == "completed"
    assert payload["current_step"] == "completed"
    assert payload["hypothesis"]["content"]["strategy_name"] == "BTC oversold rebound"
    assert payload["draft"]["version"] == "1.0"
    assert payload["sources"][0]["source_ref"] == MARTIN

    listed = client.get("/api/v1/ai/research").json()["runs"]
    assert [entry["run_id"] for entry in listed] == [run_id]
    assert "hypothesis" not in listed[0]  # the list is a summary

    one = client.get(f"/api/v1/ai/research/{run_id}")
    assert one.status_code == 200
    assert one.json()["question"] == QUESTION

    formalized = client.post("/api/v1/ai/strategy/formalize", json={"run_id": run_id})
    assert formalized.status_code == 200, formalized.text
    assert formalized.json()["draft"]["version"] == "2.0"
    assert formalized.json()["draft"]["status"] == "SUPPORTED"

    assert client.get("/api/v1/ai/research/999999").status_code == 404


def test_a_refused_answer_is_a_resource_not_an_error(client, monkeypatch):
    bad = hypothesis_payload()
    bad["rules"][1]["evidence"] = []
    patch_pipeline(monkeypatch, [bad, bad])

    response = client.post("/api/v1/ai/research", json=research_body())
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rejected"
    assert payload["hypothesis"] is None
    assert payload["draft"] is None
    assert {violation["code"] for violation in payload["violations"]} == {"evidence_missing"}


def test_research_without_a_provider_says_so(client, db_session):
    response = client.post("/api/v1/ai/research", json=research_body())
    assert response.status_code == 503
    assert response.json()["detail"] == NOT_CONFIGURED_DETAIL

    # The attempt is still a row, so the user can see it happened.
    assert db_session.scalar(select(func.count()).select_from(AIResearchRun)) == 1
    listed = client.get("/api/v1/ai/research").json()["runs"]
    assert [entry["status"] for entry in listed] == ["failed"]


def test_research_input_is_validated_before_any_model_call(client, monkeypatch):
    patch_pipeline(monkeypatch, [])

    assert client.post("/api/v1/ai/research", json=research_body(question="ab")).status_code == 422
    assert client.post("/api/v1/ai/research", json=research_body(sources=[])).status_code == 422
    assert client.post("/api/v1/ai/research", json={"question": QUESTION}).status_code == 422

    nine = [{"kind": "text", "text": "note", "source_ref": f"note-{index}"} for index in range(9)]
    assert client.post("/api/v1/ai/research", json=research_body(sources=nine)).status_code == 422
    assert client.get("/api/v1/ai/research").json()["runs"] == []


def test_formalize_reports_missing_input_and_bad_models(client, monkeypatch):
    assert client.post("/api/v1/ai/strategy/formalize", json={}).status_code == 400
    assert (
        client.post("/api/v1/ai/strategy/formalize", json={"hypothesis_id": 999}).status_code == 404
    )
    assert client.post("/api/v1/ai/strategy/formalize", json={"run_id": 999}).status_code == 404


def test_formalize_answers_422_when_the_answer_is_not_a_draft(
    client, db_session, provider, monkeypatch
):
    run, _ = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    assert run.status == "completed"

    broken = draft_payload()
    broken.pop("market")
    patch_pipeline(monkeypatch, [broken, broken])

    response = client.post("/api/v1/ai/strategy/formalize", json={"run_id": run.id})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["step"] == "strategy_formalization"
    assert "schema_invalid" in {violation["code"] for violation in detail["violations"]}
    assert db_session.get(AIResearchRun, run.id).status == "rejected"
    assert len(stored_drafts(db_session)) == 1  # only the draft the first run stored
