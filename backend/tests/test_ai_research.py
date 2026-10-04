"""The research pipeline: material in, a cited hypothesis out.

The RESEARCHER step is wired to the v1.9.7 runtime (ADR-154 … ADR-156) and may
only store an answer that survives the gate chain: schema, provenance and
disclosure. A scripted router stands in for the network, so every test here is
offline, and the Martin scenario from the plan is the reference case.
"""

from __future__ import annotations

import hashlib

import pytest
from research_payloads import (
    MARTIN,
    NOTE,
    QUESTION,
    draft_payload,
    hypothesis_payload,
    make_provider,
    note_inputs,
    run_research,
    script,
    variant,
)
from sqlalchemy import func, select

from app.ai import research as service
from app.ai.explain import AI_UNCONFIGURED
from app.ai.research_schemas import RESEARCH_SCHEMA
from app.ai.role_contracts import contract_for_role, task_output_schemas
from app.domain.models import (
    AIProvider,
    AIResearchRun,
    AITask,
    ResearchArtifact,
    ResearchArtifactFragment,
    StrategyHypothesisRule,
)
from app.domain.models import (
    StrategyDraft as StrategyDraftRow,
)
from app.domain.models import (
    StrategyHypothesis as StrategyHypothesisRow,
)


@pytest.fixture
def provider(db_session) -> AIProvider:
    return make_provider(db_session)


# --------------------------------------------------------------------- contract


def test_researcher_contract():
    contract = contract_for_role("RESEARCHER")
    assert contract.version == "1.1.0"
    assert "strategy_research" in contract.task_types
    assert task_output_schemas()["strategy_research"] is RESEARCH_SCHEMA
    assert "research.py" in contract.body
    assert "no code calls this contract" not in contract.body


# ------------------------------------------------------------------- the answer


def test_researcher_structured_output(db_session, provider):
    run, router = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])

    assert run.status == "completed"
    assert run.researcher_task_id is not None
    payload = service.run_payload(db_session, run)
    hypothesis = payload["hypothesis"]
    assert hypothesis["role"] == "RESEARCHER"
    assert hypothesis["status"] == "DRAFT"
    assert hypothesis["prompt_version"] == "1.1.0"
    assert hypothesis["content"]["strategy_name"] == "BTC oversold rebound"

    stored = db_session.scalars(
        select(StrategyHypothesisRule).where(
            StrategyHypothesisRule.hypothesis_id == hypothesis["hypothesis_id"]
        )
    ).all()
    assert {rule.rule_key for rule in stored} == {
        "r-market",
        "r-entry",
        "r-oversold",
        "r-rebound",
        "r-timeframe",
    }
    assert {rule.origin for rule in stored} == {"EXPLICIT", "ASSUMED", "UNKNOWN"}
    entry = next(rule for rule in stored if rule.rule_key == "r-entry")
    assert entry.evidence_fragment_ids_json == [MARTIN]
    assert entry.capability_status is None

    task = db_session.get(AITask, run.researcher_task_id)
    assert task.task_type == "strategy_research"
    assert task.role == "RESEARCHER"
    assert task.research_run_id == run.id
    assert task.prompt_name == "strategy_research"

    assert [source.ref for source in router.calls[0].untrusted_sources] == [MARTIN]
    assert router.calls[0].structured_facts["question"] == QUESTION


def test_the_material_is_kept_as_artifacts_and_excerpts_not_a_copy(db_session, provider):
    run, _ = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])

    artifacts = db_session.scalars(select(ResearchArtifact)).all()
    assert [artifact.source_ref for artifact in artifacts] == [MARTIN]
    assert artifacts[0].text_hash and artifacts[0].size_bytes == len(NOTE)
    fragments = db_session.scalars(select(ResearchArtifactFragment)).all()
    assert fragments
    assert all(len(fragment.text_excerpt) <= service.EXCERPT_CHARS for fragment in fragments)
    assert "text" not in run.sources_json[0]
    assert run.sources_json[0]["characters_read"] == len(NOTE)


def test_material_from_elsewhere_is_kept_as_a_short_excerpt(db_session, provider):
    """Someone else's text: metadata, hashes, and at most 500 characters (ADR-161)."""

    paper = "\n".join(f"第 {index} 行：外部资料的一段说明。" for index in range(200))
    run, _ = run_research(
        db_session,
        provider,
        [hypothesis_payload(), draft_payload()],
        inputs=[
            service.ResearchInput(text=NOTE, source_ref=MARTIN),
            service.ResearchInput(
                text=paper, source_ref="paper", kind="text", uri="https://example.com/paper"
            ),
        ],
    )

    assert run.status == "completed"
    artifact = db_session.scalars(
        select(ResearchArtifact).where(ResearchArtifact.source_ref == "paper")
    ).one()
    assert artifact.source_hash == hashlib.sha256(paper.encode("utf-8")).hexdigest()
    # The run read all of it; it is the *keeping* that is bounded.
    assert artifact.text_hash == artifact.source_hash
    excerpts = db_session.scalars(
        select(ResearchArtifactFragment).where(ResearchArtifactFragment.artifact_id == artifact.id)
    ).all()
    kept = sum(len(fragment.text_excerpt) for fragment in excerpts)
    assert 0 < kept <= service.THIRD_PARTY_EXCERPT_CHARS
    assert kept < len(paper)
    assert "第 199 行" not in "".join(fragment.text_excerpt for fragment in excerpts)

    limited = [warning for warning in run.warnings_json if warning["kind"] == "excerpt_limited"]
    assert [warning["source_ref"] for warning in limited] == ["paper"]
    assert limited[0]["policy"] == "excerpt"
    assert limited[0]["retention_chars"] == service.THIRD_PARTY_EXCERPT_CHARS
    assert limited[0]["read_chars"] == len(paper)

    meta = next(entry for entry in run.sources_json if entry["source_ref"] == "paper")
    assert meta["retention"] == {
        "policy": "excerpt",
        "excerpt_budget": service.THIRD_PARTY_EXCERPT_CHARS,
        "stored_chars": kept,
        "full_text_stored": False,
    }


def test_the_users_own_material_is_kept_in_full(db_session, provider):
    """The user's own note is not third-party material, so it is not trimmed."""

    own = NOTE + "".join(f"我的补充第 {index} 行。" for index in range(60))
    run, _ = run_research(
        db_session,
        provider,
        [hypothesis_payload(), draft_payload()],
        inputs=[service.ResearchInput(text=own, source_ref=MARTIN)],
        question=variant(QUESTION, "自己的长笔记"),
    )

    assert run.status == "completed"
    artifact = db_session.scalars(select(ResearchArtifact)).one()
    assert artifact.source_hash == artifact.text_hash
    assert artifact.size_bytes == len(own)
    excerpts = db_session.scalars(select(ResearchArtifactFragment)).all()
    kept = sum(len(fragment.text_excerpt) for fragment in excerpts)
    joined = "".join(fragment.text_excerpt for fragment in excerpts)
    assert "我的补充第 59 行。" in joined  # the end of the note was kept too
    assert kept == len(own)
    assert [warning for warning in run.warnings_json if warning["kind"] == "excerpt_limited"] == []


def test_material_the_user_is_licensed_to_keep_may_be_kept_in_full(db_session, provider):
    """Third-party material may be kept whole, but only with a licence note."""

    paper = "".join(f"第 {index} 行：我方拥有的资料。\n" for index in range(60))
    run, _ = run_research(
        db_session,
        provider,
        [hypothesis_payload(), draft_payload()],
        inputs=[
            service.ResearchInput(text=NOTE, source_ref=MARTIN),
            service.ResearchInput(
                text=paper,
                kind="text",
                source_ref="paper",
                retention="full",
                license_note="the user pasted it and owns it",
            ),
        ],
        question=variant(QUESTION, "自有资料全文"),
    )

    assert run.status == "completed"
    artifact = db_session.scalars(
        select(ResearchArtifact).where(ResearchArtifact.source_ref == "paper")
    ).one()
    assert artifact.license_note == "the user pasted it and owns it"
    excerpts = db_session.scalars(
        select(ResearchArtifactFragment).where(ResearchArtifactFragment.artifact_id == artifact.id)
    ).all()
    kept = sum(len(fragment.text_excerpt) for fragment in excerpts)
    assert kept > service.THIRD_PARTY_EXCERPT_CHARS
    assert [warning for warning in run.warnings_json if warning["kind"] == "excerpt_limited"] == []
    meta = next(entry for entry in run.sources_json if entry["source_ref"] == "paper")
    assert meta["retention"]["policy"] == "full"


# ------------------------------------------------------------------- provenance


def test_researcher_provenance(db_session, provider):
    """An EXPLICIT or INFERRED rule has to cite material this run supplied."""

    uncited = hypothesis_payload()
    uncited["rules"][1]["evidence"] = []
    run, _ = run_research(db_session, provider, [uncited, uncited])

    assert run.status == "rejected"
    assert "evidence_missing" in {violation["code"] for violation in run.violations_json}
    assert db_session.scalar(select(func.count()).select_from(StrategyHypothesisRow)) == 0

    invented = hypothesis_payload()
    invented["rules"][1]["evidence"] = [{"source_ref": "some-other-paper"}]
    other_run, _ = run_research(
        db_session,
        provider,
        [invented, invented],
        question=variant(QUESTION, "引用了不存在的材料"),
    )

    assert other_run.status == "rejected"
    assert "evidence_unknown_source" in {
        violation["code"] for violation in other_run.violations_json
    }


def test_an_invented_quote_is_refused(db_session, provider):
    """A real ``source_ref`` with a made-up sentence is not evidence (ADR-159).

    Before this the server only checked that the citation pointed at material the
    run supplied; the words themselves were never looked up, so a model could
    label its own invention EXPLICIT by attaching a plausible-sounding quote.
    """

    invented = hypothesis_payload()
    invented["rules"][1]["evidence"] = [
        {"source_ref": MARTIN, "quote": "超跌之后立刻满仓买入，不要止损"}
    ]
    run, _ = run_research(db_session, provider, [invented, invented])

    assert run.status == "rejected"
    assert {violation["code"] for violation in run.violations_json} == {"evidence_mismatch"}
    assert db_session.scalar(select(func.count()).select_from(StrategyHypothesisRow)) == 0


def test_an_explicit_rule_has_to_quote_the_material(db_session, provider):
    """A citation with no words in it is not a citation."""

    silent = hypothesis_payload()
    silent["rules"][1]["evidence"] = [{"source_ref": MARTIN}]
    run, _ = run_research(
        db_session, provider, [silent, silent], question=variant(QUESTION, "没有引文")
    )

    assert run.status == "rejected"
    assert {violation["code"] for violation in run.violations_json} == {"evidence_missing_quote"}


def test_a_verified_quote_records_where_it_was_found(db_session, provider):
    """Line breaks in a copied quote are forgiven; the span and the version are kept."""

    spaced = hypothesis_payload()
    spaced["rules"][1]["evidence"] = [
        {"source_ref": MARTIN, "quote": "BTC\n\n超跌之后反弹的时候买入"}
    ]
    run, _ = run_research(
        db_session, provider, [spaced, draft_payload()], question=variant(QUESTION, "引文换行")
    )

    assert run.status == "completed"
    content = service.run_payload(db_session, run)["hypothesis"]["content"]
    evidence = content["rules"][1]["evidence"][0]
    assert evidence["verified"] is True
    assert evidence["verified_against"] == hashlib.sha256(NOTE.encode("utf-8")).hexdigest()
    assert NOTE[evidence["char_start"] : evidence["char_end"]] == "BTC 超跌之后反弹的时候买入"


def test_an_assumed_rule_cannot_hide_in_the_assumptions_list(db_session, provider):
    silent = hypothesis_payload()
    silent["assumptions"] = []
    run, _ = run_research(db_session, provider, [silent, silent])

    assert run.status == "rejected"
    assert "assumed_not_disclosed" in {violation["code"] for violation in run.violations_json}


def test_a_rule_the_model_cannot_place_must_be_unknown(db_session, provider):
    silent = hypothesis_payload()
    silent["unknowns"] = [
        unknown for unknown in silent["unknowns"] if unknown["field"] != "timeframe"
    ]
    run, _ = run_research(db_session, provider, [silent, silent])

    assert run.status == "rejected"
    assert "unknown_not_disclosed" in {violation["code"] for violation in run.violations_json}


def test_a_proposed_definition_is_labelled_assumed_not_explicit(db_session, provider):
    """The Martin guard: a definition the AI invented may not become the user's rule."""

    inflated = hypothesis_payload()
    inflated["rules"][2]["origin"] = "EXPLICIT"
    inflated["rules"][2].pop("evidence", None)
    run, _ = run_research(db_session, provider, [inflated, inflated])

    assert run.status == "rejected"
    codes = {violation["code"] for violation in run.violations_json}
    assert "evidence_missing" in codes
    assert db_session.scalar(select(func.count()).select_from(StrategyHypothesisRow)) == 0


# --------------------------------------------------------------------- unknowns


def test_researcher_unknowns(db_session, provider):
    run, _ = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    content = service.run_payload(db_session, run)["hypothesis"]["content"]

    assert {"timeframe", "exit", "risk", "sizing"} <= {
        unknown["field"] for unknown in content["unknowns"]
    }
    assert all(unknown["needed_to_formalize"] for unknown in content["unknowns"])

    explicit = [rule for rule in content["rules"] if rule["origin"] == "EXPLICIT"]
    assumed = [rule for rule in content["rules"] if rule["origin"] == "ASSUMED"]
    assert explicit and assumed
    # The user's own words carry no thresholds; the thresholds live in the AI's
    # proposal, which is marked as a proposal.
    assert all(not rule.get("parameters") for rule in explicit)
    assert all("RSI" in rule["statement"] for rule in assumed)
    assert any("不是 Martin" in assumption["statement"] for assumption in content["assumptions"])
    assert content["confidence"] == "low"


def test_researcher_ambiguities(db_session, provider):
    run, _ = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    content = service.run_payload(db_session, run)["hypothesis"]["content"]

    assert {item["phrase"] for item in content["ambiguities"]} == {"超跌", "反弹"}
    for ambiguity in content["ambiguities"]:
        assert len(ambiguity["readings"]) >= 2
        assert ambiguity["needs_decision"] is True

    empty = hypothesis_payload()
    empty["ambiguities"][0]["readings"] = []
    refused, _ = run_research(
        db_session,
        provider,
        [empty, empty],
        question=variant(QUESTION, "没有列出读法"),
    )
    assert refused.status == "rejected"
    assert "domain_invalid" in {violation["code"] for violation in refused.violations_json}


# ------------------------------------------------------------- the retry policy


def test_a_refused_answer_is_retried_once_and_then_the_run_is_rejected(db_session, provider):
    bad = hypothesis_payload()
    bad["rules"][1]["evidence"] = []
    run, router = run_research(db_session, provider, [bad, bad])

    assert run.status == "rejected"
    assert run.attempts == 2
    assert len(router.calls) == 2
    assert "refused" in router.calls[1].user_prompt
    assert router.calls[1].user_prompt != router.calls[0].user_prompt
    assert run.completed_at is not None
    tasks = db_session.scalars(select(AITask)).all()
    assert len(tasks) == 2
    assert all(task.research_run_id == run.id for task in tasks)
    assert db_session.scalar(select(func.count()).select_from(StrategyDraftRow)) == 0


def test_a_corrected_second_answer_is_accepted(db_session, provider):
    bad = hypothesis_payload()
    bad["rules"][1]["evidence"] = []
    run, router = run_research(db_session, provider, [bad, hypothesis_payload(), draft_payload()])

    assert run.status == "completed"
    assert run.attempts == 3  # two researcher attempts, one architect call
    assert len(router.calls) == 3
    assert [call.task_type for call in router.calls] == [
        "strategy_research",
        "strategy_research",
        "strategy_formalization",
    ]


# ---------------------------------------------------------------- input handling


def test_a_run_needs_a_question_and_a_source(db_session, provider):
    _router, factory = script([])
    with pytest.raises(ValueError, match="needs a question"):
        service.start_research(
            db_session, question="   ", inputs=note_inputs(), router_factory=factory
        )
    with pytest.raises(ValueError, match="at least one source"):
        service.start_research(db_session, question=QUESTION, inputs=[], router_factory=factory)
    assert db_session.scalar(select(func.count()).select_from(AIResearchRun)) == 0


def test_a_source_with_nothing_to_read_is_named(db_session, provider):
    _router, factory = script([])
    # A fetched kind needs a uri, a snapshot, or the text itself: a bare pdf says
    # nothing about what should be read, so it is refused by name.
    with pytest.raises(ValueError, match="a 'pdf' source needs a uri, a snapshot_id"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[service.ResearchInput(kind="pdf", source_ref="paper")],
            router_factory=factory,
        )
    with pytest.raises(ValueError, match="a 'url' source needs a uri, a snapshot_id"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[service.ResearchInput(kind="url", source_ref="page")],
            router_factory=factory,
        )
    with pytest.raises(ValueError, match="unknown source kind"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[service.ResearchInput(text="x", kind="screenshot", source_ref="shot")],
            router_factory=factory,
        )
    with pytest.raises(ValueError, match="at most 8 sources"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[
                service.ResearchInput(text="x", source_ref=f"note-{index}") for index in range(9)
            ],
            router_factory=factory,
        )
    with pytest.raises(ValueError, match="duplicate source_ref"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[
                service.ResearchInput(text="a", source_ref="same"),
                service.ResearchInput(text="b", source_ref="same"),
            ],
            router_factory=factory,
        )


def test_a_retention_policy_has_to_be_one_this_version_knows(db_session, provider):
    """Keeping someone else's material whole needs a reason and a licence note."""

    _router, factory = script([])
    with pytest.raises(ValueError, match="unknown retention policy 'forever'"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[service.ResearchInput(text="x", source_ref="paper", retention="forever")],
            router_factory=factory,
        )
    with pytest.raises(ValueError, match="needs a license note"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[
                service.ResearchInput(text="x", kind="text", source_ref="paper", retention="full")
            ],
            router_factory=factory,
        )
    assert db_session.scalar(select(func.count()).select_from(AIResearchRun)) == 0


def test_a_long_source_is_truncated_and_the_run_says_so(db_session, provider):
    long_text = "x" * (service.MAX_ARTIFACT_CHARS + 500)
    run, _ = run_research(
        db_session,
        provider,
        [hypothesis_payload(), draft_payload()],
        inputs=[
            service.ResearchInput(text=NOTE, source_ref=MARTIN),
            service.ResearchInput(text=long_text, source_ref="big"),
        ],
    )

    assert run.status == "completed"
    warnings = [warning for warning in run.warnings_json if warning["kind"] == "truncated"]
    assert len(warnings) == 1
    assert warnings[0]["source_ref"] == "big"
    assert warnings[0]["kept_chars"] == service.MAX_ARTIFACT_CHARS
    big = next(entry for entry in run.sources_json if entry["source_ref"] == "big")
    assert big["size_bytes"] == len(long_text)
    assert big["characters_read"] == service.MAX_ARTIFACT_CHARS

    # Two hashes, two questions: what the caller handed over, and what was read
    # (citations are checked against the second one) — ADR-161.
    artifact = db_session.scalars(
        select(ResearchArtifact).where(ResearchArtifact.source_ref == "big")
    ).one()
    assert artifact.source_hash == hashlib.sha256(long_text.encode("utf-8")).hexdigest()
    assert (
        artifact.text_hash
        == hashlib.sha256(long_text[: service.MAX_ARTIFACT_CHARS].encode("utf-8")).hexdigest()
    )
    assert artifact.source_hash != artifact.text_hash


def test_a_run_without_a_provider_is_recorded_not_raised(db_session):
    run = service.start_research(db_session, question=QUESTION, inputs=note_inputs(), providers=[])
    db_session.commit()

    assert run.status == "failed"
    assert run.error_message == AI_UNCONFIGURED
    assert run.attempts == 0
    assert run.sources_json  # what was read is still recorded
    assert db_session.scalar(select(func.count()).select_from(AITask)) == 0


def test_a_provider_failure_leaves_a_failed_run_not_a_hypothesis(db_session, provider):
    run, _ = run_research(db_session, provider, [RuntimeError("provider is down")])

    assert run.status == "failed"
    assert "provider is down" in (run.error_message or "")
    assert db_session.scalar(select(func.count()).select_from(StrategyHypothesisRow)) == 0
    task = db_session.scalars(select(AITask)).one()
    assert task.status == "failed"
    assert task.research_run_id == run.id
