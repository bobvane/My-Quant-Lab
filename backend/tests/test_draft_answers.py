"""Answering the compiler's questions: a new draft, never a rewritten one (ADR-211).

``docs/34`` gap G7: the compiler already reports machine-readable questions
(``docs/29`` §12) and refuses to answer them. This module pins the channel that
closes the loop -- a person answers, the server materialises the answers into a
**new** ``strategy_drafts`` row of the same run, and the answered draft keeps its
own content, its own hash and its own confirmation record.

The gates are the point, so the tests are split the same way the code is: the pure
engine (``build_questions`` / ``apply_answers``) and the transaction boundary
(``answer_draft_questions`` through its two endpoints).
"""

from __future__ import annotations

from typing import Any

import pytest
from research_payloads import hypothesis_payload
from sqlalchemy import func, select

from app.ai import confirmation as confirmation_service
from app.ai import draft_answers
from app.domain.models import (
    AIResearchRun,
    AuditLog,
    Strategy,
    StrategyDraft,
    StrategyHypothesis,
    StrategyVersion,
)

QUESTIONS_URL = "/api/v1/ai/strategy/drafts/{draft_id}/questions"
ANSWERS_URL = "/api/v1/ai/strategy/drafts/{draft_id}/answers"

#: The slots the open fixture asks about, and the control each one needs. A person
#: can answer every one of them, which is what makes it the happy path.
OPEN_SLOTS = (
    "indicators.rsi14.input",
    "market.timeframes",
    "exit.long",
    "risk",
    "sizing",
    "sizing.mode",
    "execution.fee_bps",
    "execution.slippage_bps",
)

#: The same assertions, run twice: in-memory SQLite always, and -- when the CI job
#: offers a scratch PostgreSQL through ``TEST_POSTGRES_URL`` -- PostgreSQL on the
#: real migration chain. The answer channel writes a JSON payload into a row of a
#: dozen columns and appends an audit entry, which is exactly the shape that has
#: twice shipped green here and failed on the deployed database: a column narrower
#: than the value written to it (ADR-064, ADR-186). ``conftest.db_session`` decides
#: the backend; nothing in this module knows the difference.
pytestmark = pytest.mark.postgres


def _entry_rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": "d-entry",
        "field": "entry",
        "statement": "在超跌之后的反弹里买入（人工可改的规范化写法）。",
        "origin": "ASSUMED",
        "confidence": "medium",
        "derived_from": "r-entry",
        "parameters": {
            "left": "RSI",
            "operator": "lt",
            "right": 30,
            "period": 14,
            "side": "long",
        },
    }
    rule.update(overrides)
    return rule


def _open_payload() -> dict[str, Any]:
    """A draft that passed the research gate and still asks a person eight things.

    Every rule is ``ASSUMED`` (nothing cites a source, which is what the evidence
    gate requires of anything that is not) and the two rules the hypothesis states
    ``EXPLICIT`` derive from it, so the draft is not silently dropping intent.
    """

    return {
        "strategy_name": "BTC oversold rebound (RSI formalization)",
        "status": "SUPPORTED",
        #: No ``markets``/``universe`` here: the compiler reports those as
        #: ``not_expressible`` (DSL 1.0 has no field for an instrument), which would
        #: make this draft structurally un-compilable whatever a person answers. The
        #: happy path needs a draft whose only open questions are answerable ones.
        "market": {
            "asset_classes": ["crypto"],
        },
        "rules": [
            {
                "id": "d-market",
                "field": "market",
                "statement": "标的为 BTC，材料里说了。",
                "origin": "ASSUMED",
                "derived_from": "r-market",
                "parameters": {},
            },
            _entry_rule(),
        ],
        "unknowns": [
            {"field": "timeframe", "why": "材料没有说明周期。", "needed_to_formalize": True},
            {"field": "exit", "why": "材料没有说明卖出条件。", "needed_to_formalize": True},
            {"field": "risk", "why": "材料没有说明止损。", "needed_to_formalize": True},
            {"field": "sizing", "why": "材料没有说明仓位。", "needed_to_formalize": True},
        ],
        "indicators": [{"name": "RSI", "origin": "ASSUMED", "parameters": {"period": 14}}],
        "required_capabilities": [],
        "experimental_alternatives": [],
        #: The gate refuses an ASSUMED rule that no assumption names
        #: (``assumed_not_disclosed``, and the vocabulary is ``rules[].field``, never a
        #: rule id -- ADR-154). The AI's own drafts disclose theirs the same way.
        "assumptions": [
            {
                "statement": "标的按材料里的 BTC 记。",
                "applies_to": ["market"],
                "reason": "材料只给了标的，没说市场和周期。",
            },
            {
                "statement": "入场条件按超跌反弹的读法写成 RSI 下穿 30。",
                "applies_to": ["entry"],
                "reason": "材料只说了超跌买入，没给指标和阈值。",
            },
        ],
        "parameters": {},
        "notes": [],
        "understanding_of_original": "Martin 想在超跌之后的反弹里买入 BTC。",
    }


def _short_payload() -> dict[str, Any]:
    """The same draft, entered short: the compiler asks for a slot nothing can carry."""

    payload = _open_payload()
    payload["rules"][1] = _entry_rule(
        parameters={
            "left": "RSI",
            "operator": "lt",
            "right": 30,
            "period": 14,
            "side": "short",
        }
    )
    return payload


def _complete_payload() -> dict[str, Any]:
    """A draft that compiles: every block is filled, nothing is unknown."""

    payload = _open_payload()
    payload["market"]["timeframes"] = ["1d"]
    payload["indicators"] = [
        {"name": "RSI", "origin": "ASSUMED", "parameters": {"period": 14, "input": "close"}}
    ]
    payload["rules"] = payload["rules"] + [
        {
            "id": "x-overbought",
            "field": "exit",
            "statement": "RSI 上穿 70 就卖出。",
            "origin": "ASSUMED",
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
            "statement": "两个 ATR 的止损，单笔最多一半仓位。",
            "origin": "ASSUMED",
            "parameters": {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
        },
        {
            "id": "s-sizing",
            "field": "sizing",
            "statement": "每次投入三成。",
            "origin": "ASSUMED",
            "parameters": {"mode": "fixed_fraction", "fraction": 0.3},
        },
        {
            "id": "c-costs",
            "field": "execution",
            "statement": "十个基点手续费、五个基点滑点。",
            "origin": "ASSUMED",
            "parameters": {"fee_bps": 10, "slippage_bps": 5},
        },
    ]
    payload["unknowns"] = []
    #: Not gate-valid (``unknowns: []`` drops the hypothesis' ``r-timeframe``), but this
    #: fixture only ever goes through the compile endpoint, which never runs the gate.
    payload["assumptions"] = payload["assumptions"] + [
        {
            "statement": f"{field} 这一块按常见默认值补齐。",
            "applies_to": [field],
            "reason": "编译用草案，填写完整。",
        }
        for field in ("exit", "risk", "sizing", "execution")
    ]
    return payload


def _hypothesis(*, unknown_timeframe: bool = True, risk_rules: int = 0) -> dict[str, Any]:
    """The stored hypothesis, with hooks for the cases a draft cannot satisfy."""

    data = hypothesis_payload()
    rules = list(data["rules"])
    if not unknown_timeframe:
        rules = [rule for rule in rules if rule["id"] != "r-timeframe"]
    for index in range(risk_rules):
        rules.append(
            {
                "id": f"r-risk-{index + 1}",
                "field": "risk",
                "statement": "材料里说了止损，但没说具体数字。",
                "origin": "EXPLICIT",
                "confidence": "high",
            }
        )
    data["rules"] = rules
    return data


def _store(
    db_session,
    payload: dict[str, Any],
    *,
    hypothesis: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> tuple[AIResearchRun, StrategyDraft]:
    """A run with one stored draft, built the way the research layer builds one."""

    run = AIResearchRun(question="为什么这个策略会有效？", status="drafted")
    db_session.add(run)
    db_session.flush()
    row = StrategyHypothesis(
        run_id=run.id,
        strategy_name="BTC oversold rebound",
        hypothesis_json=hypothesis if hypothesis is not None else _hypothesis(),
    )
    db_session.add(row)
    db_session.flush()
    draft = StrategyDraft(
        run_id=run.id,
        hypothesis_id=row.id,
        version="1.0",
        status="SUPPORTED",
        capability_status="SUPPORTED",
        draft_json=payload,
    )
    db_session.add(draft)
    db_session.flush()
    run.draft_id = draft.id
    if confirmed:
        confirmation_service.record_confirmation(db_session, draft=draft, decision="confirmed")
    db_session.commit()
    return run, draft


def _answers(draft: StrategyDraft, *entries: tuple[str, str, Any]) -> dict[str, Any]:
    return {
        "answered_draft_hash": draft_answers.draft_hash_of(draft.draft_json),
        "answers": [{"code": code, "slot": slot, "value": value} for code, slot, value in entries],
    }


def _draft_rows(db_session, run_id: int) -> list[StrategyDraft]:
    return list(
        db_session.scalars(
            select(StrategyDraft).where(StrategyDraft.run_id == run_id).order_by(StrategyDraft.id)
        ).all()
    )


def _audit_rows(db_session, event_type: str = "strategy_draft_answered") -> list[AuditLog]:
    return list(db_session.scalars(select(AuditLog).where(AuditLog.event_type == event_type)).all())


def _count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model)) or 0


# --- A. What the compiler asks, and what a person can answer ------------------------


def test_the_open_draft_asks_only_questions_a_person_can_answer() -> None:
    payload = _open_payload()

    report = draft_answers.report_for_questions(payload)
    questions = draft_answers.build_questions(report, payload)

    assert report["result"] == "NEEDS_USER_DECISION"
    assert tuple(question.slot for question in questions) == OPEN_SLOTS
    assert all(question.answerable for question in questions)


def test_a_slot_the_compiler_asks_twice_is_one_question() -> None:
    """``risk`` is both an admitted unknown and an empty block; a person answers once."""

    payload = _open_payload()
    questions = draft_answers.build_questions(draft_answers.report_for_questions(payload), payload)
    risk = next(question for question in questions if question.slot == "risk")

    assert set(risk.codes) == {"unknown_blocks_slot", "missing_required_slot"}
    # The strict code wins: a block the compiler needs a value for cannot be settled
    # by saying "keep it unknown".
    assert risk.code == "missing_required_slot"
    assert risk.value_optional is False
    assert [key.name for key in risk.keys] == [
        "stop_loss_atr_multiple",
        "take_profit_r_multiple",
        "take_profit_atr_multiple",
        "max_position_pct",
    ]


def test_a_slot_only_an_unknown_asked_may_be_left_unknown() -> None:
    payload = _open_payload()
    questions = draft_answers.build_questions(draft_answers.report_for_questions(payload), payload)
    sizing = next(question for question in questions if question.slot == "sizing")

    assert sizing.codes == ("unknown_blocks_slot",)
    assert sizing.value_optional is True


def test_the_short_entry_slot_is_listed_but_refused_with_a_reason() -> None:
    """``market.allow_short`` is unanswerable in this version, and says why (ADR-138)."""

    payload = _short_payload()
    questions = draft_answers.build_questions(draft_answers.report_for_questions(payload), payload)
    question = next(item for item in questions if item.slot == "market.allow_short")

    assert question.answerable is False
    assert question.kind == "unsupported"
    assert "docs/29" in question.reason

    outcome = draft_answers.apply_answers(
        payload,
        [draft_answers.Answer("missing_required_slot", "market.allow_short", True)],
        questions=questions,
    )
    assert [problem.code for problem in outcome.problems] == ["answer_not_supported"]
    assert outcome.payload == payload


@pytest.mark.parametrize(
    "item",
    [
        {"code": "rule_unmapped", "slot": "entry.long", "detail": "no parameters"},
        {"code": "parameter_invalid", "slot": "risk.stop_loss_atr_multiple", "detail": "…"},
        {"code": "not_expressible", "slot": "market.universe", "detail": "…"},
    ],
)
def test_a_structural_rejection_is_never_a_question(item: dict[str, Any]) -> None:
    payload = _open_payload()

    question = draft_answers.question_for({**item, "user_decidable": True}, payload=payload)

    assert question.answerable is False
    assert question.reason


def test_the_question_list_never_names_a_slot_the_report_did_not() -> None:
    payload = _open_payload()
    report = draft_answers.report_for_questions(payload)

    asked = {entry["slot"] for entry in report["rejections"] if entry["user_decidable"]}
    for question in draft_answers.build_questions(report, payload):
        assert question.slot in asked


# --- B. Applying the answers (pure) -------------------------------------------------


def _open_answers() -> list[tuple[str, str, Any]]:
    return [
        ("missing_required_slot", "indicators.rsi14.input", "close"),
        ("unknown_blocks_slot", "market.timeframes", "4h"),
        ("missing_required_slot", "exit.long", {"left": "RSI", "operator": "gt", "right": 70}),
        (
            "missing_required_slot",
            "risk",
            {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
        ),
        ("unknown_blocks_slot", "sizing", {"mode": "fixed_fraction", "fraction": 0.3}),
        ("missing_required_slot", "execution.fee_bps", 10),
        ("missing_required_slot", "execution.slippage_bps", 5),
    ]


def _apply(payload: dict[str, Any], entries: list[tuple[str, str, Any]]):
    questions = draft_answers.build_questions(draft_answers.report_for_questions(payload), payload)
    return draft_answers.apply_answers(
        payload,
        [draft_answers.Answer(code, slot, value) for code, slot, value in entries],
        questions=questions,
    )


def test_answering_writes_the_value_and_stops_the_question() -> None:
    payload = _open_payload()
    original = draft_answers.draft_hash_of(payload)

    outcome = _apply(payload, _open_answers())

    assert outcome.problems == ()
    new_payload = outcome.payload
    # The draft payload handed in is never touched.
    assert draft_answers.draft_hash_of(payload) == original
    assert new_payload is not payload

    by_field = {
        rule["field"]: rule for rule in new_payload["rules"] if rule["id"].startswith("human-")
    }
    assert by_field["risk"]["parameters"] == {
        "stop_loss_atr_multiple": 2.0,
        "max_position_pct": 0.5,
    }
    assert by_field["exit"]["parameters"] == {
        "left": "RSI",
        "operator": "gt",
        "right": 70,
        "side": "long",
    }
    assert by_field["execution"]["parameters"] == {"fee_bps": 10.0, "slippage_bps": 5.0}

    # A rule no model wrote: assumed, cited by nobody, and said to be human.
    for rule in by_field.values():
        assert rule["origin"] == "ASSUMED"
        assert rule["derived_from"] is None
        assert rule["evidence"] == []
        assert "人工回答" in rule["statement"]
        assert "ADR-211" in rule["note"]

    assert new_payload["market"]["timeframes"] == ["4h"]
    assert new_payload["indicators"][0]["parameters"] == {"period": 14, "input": "close"}

    # The unknowns stay (they are what tells the gate the material was silent) and
    # only stop asking.
    settled = {unknown["field"] for unknown in new_payload["unknowns"]}
    assert settled == {"timeframe", "exit", "risk", "sizing"}
    assert all(unknown["needed_to_formalize"] is False for unknown in new_payload["unknowns"])

    assert draft_answers.report_for_questions(new_payload)["result"] == "COMPILED"


def test_a_refused_answer_leaves_the_whole_payload_alone() -> None:
    payload = _open_payload()
    entries = _open_answers()[:-1] + [("missing_required_slot", "execution.slippage_bps", -5)]

    outcome = _apply(payload, entries)

    assert [problem.code for problem in outcome.problems] == ["answer_invalid"]
    assert outcome.payload == payload
    assert outcome.applied == ()


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ([("missing_required_slot", "sizing.mode", "fixed_fraction")], None),
        ([("missing_required_slot", "not.a.slot", 1)], "answer_not_a_question"),
        ([("made_up_code", "risk.max_position_pct", 0.5)], "answer_not_a_question"),
        (
            [
                ("missing_required_slot", "execution.fee_bps", 10),
                ("unknown_blocks_slot", "execution.fee_bps", 12),
            ],
            "answer_duplicate",
        ),
        ([("missing_required_slot", "execution.fee_bps", 2000)], "answer_invalid"),
        ([("missing_required_slot", "execution.fee_bps", True)], "answer_invalid"),
        ([("unknown_blocks_slot", "risk", {"max_position_pct": 1.5})], "answer_invalid"),
        ([("unknown_blocks_slot", "sizing", {"mode": "no_such_mode"})], "answer_invalid"),
        ([("unknown_blocks_slot", "sizing", {"nonsense": 1})], "answer_invalid"),
        ([("unknown_blocks_slot", "sizing", {})], "answer_invalid"),
        (
            [
                (
                    "missing_required_slot",
                    "exit.long",
                    {"left": "RSI", "operator": "gt", "right": 70, "dsl": "x"},
                )
            ],
            "answer_invalid",
        ),
        ([("missing_required_slot", "exit.long", {"left": "RSI", "right": 70})], "answer_invalid"),
        (
            [
                (
                    "missing_required_slot",
                    "exit.long",
                    {"left": "RSI", "operator": "gt", "right": "70"},
                )
            ],
            "answer_invalid",
        ),
        ([("unknown_blocks_slot", "market.timeframes", "2d")], "answer_invalid"),
        ([("missing_required_slot", "indicators.rsi14.input", "")], "answer_invalid"),
    ],
)
def test_an_answer_that_is_not_usable_is_refused(entries, code) -> None:
    payload = _open_payload()

    outcome = _apply(payload, entries)

    if code is None:
        assert outcome.problems == ()
        return
    assert [problem.code for problem in outcome.problems] == [code]
    assert outcome.payload == payload


def test_a_block_the_draft_already_decided_is_a_conflict_not_an_overwrite() -> None:
    # The form was filled against the open draft; by the time the answers land the
    # draft carries a risk rule of its own. This guard is what stops a silent
    # overwrite of a value somebody already decided.
    asked = _open_payload()
    questions = draft_answers.build_questions(draft_answers.report_for_questions(asked), asked)
    payload = _complete_payload()

    outcome = draft_answers.apply_answers(
        payload,
        [draft_answers.Answer("missing_required_slot", "risk", {"max_position_pct": 0.25})],
        questions=questions,
    )

    assert [problem.code for problem in outcome.problems] == ["answer_conflict"]
    assert "k-risk" in outcome.problems[0].message
    assert outcome.payload == payload


def test_a_second_condition_on_the_same_side_is_a_conflict() -> None:
    asked = _open_payload()
    questions = draft_answers.build_questions(draft_answers.report_for_questions(asked), asked)
    payload = _complete_payload()

    outcome = draft_answers.apply_answers(
        payload,
        [
            draft_answers.Answer(
                "missing_required_slot",
                "exit.long",
                {"left": "RSI", "operator": "gt", "right": 60},
            )
        ],
        questions=questions,
    )

    assert [problem.code for problem in outcome.problems] == ["answer_conflict"]
    assert outcome.payload == payload


def test_an_indicator_the_draft_never_declared_cannot_be_answered() -> None:
    payload = _open_payload()
    report = draft_answers.report_for_questions(payload)
    forged = dict(report["rejections"][0])
    forged["slot"] = "indicators.never_declared.input"

    question = draft_answers.question_for(forged, payload=payload)

    assert question.answerable is False
    assert "指标声明" in question.reason


def test_saying_still_unknown_clears_the_question_without_a_value() -> None:
    payload = _open_payload()

    outcome = _apply(payload, [("unknown_blocks_slot", "sizing", None)])

    assert outcome.problems == ()
    assert outcome.applied[0].value is None
    assert outcome.applied[0].unknown_fields == ("sizing",)
    assert outcome.payload["unknowns"][3]["needed_to_formalize"] is False


def test_a_question_that_needs_a_value_cannot_be_answered_with_an_empty_one() -> None:
    payload = _open_payload()

    outcome = _apply(payload, [("missing_required_slot", "risk", None)])

    assert [problem.code for problem in outcome.problems] == ["answer_invalid"]
    assert outcome.payload == payload


def test_no_answers_at_all_is_refused() -> None:
    payload = _open_payload()

    outcome = _apply(payload, [])

    assert [problem.code for problem in outcome.problems] == ["answer_invalid"]


# --- C. The transaction boundary ----------------------------------------------------


def test_answering_creates_a_new_draft_and_leaves_the_old_one_alone(client, db_session) -> None:
    payload = _open_payload()
    run, draft = _store(db_session, payload, confirmed=True)
    old_hash = draft_answers.draft_hash_of(payload)

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers())
    )

    assert response.status_code == 201
    body = response.json()
    assert body["previous_draft_id"] == draft.id
    assert body["previous_draft_hash"] == old_hash
    assert body["needs_confirmation"] is True
    assert body["strategy_version_created"] is False
    assert body["remaining_questions"] == []
    assert body["draft"]["version"] == "2.0"
    assert body["draft"]["status"] == "SUPPORTED"
    # A person decided; nothing here may look like a model did.
    assert body["draft"]["ai_task_id"] is None
    assert body["draft"]["model"] is None
    assert body["draft"]["compiled_strategy_version_id"] is None
    assert body["draft"]["confirmation"] is None

    rows = _draft_rows(db_session, run.id)
    assert [row.id for row in rows] == [draft.id, body["draft"]["draft_id"]]
    db_session.expire_all()
    assert draft.draft_json == payload
    assert draft_answers.draft_hash_of(draft.draft_json) == old_hash
    assert draft.compiled_strategy_version_id is None
    assert draft.version == "1.0"
    assert run.draft_id == body["draft"]["draft_id"]


def test_the_old_drafts_confirmation_does_not_authorize_the_new_draft(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload(), confirmed=True)
    strategy = Strategy(name="Answered target", slug="answered-target")
    db_session.add(strategy)
    db_session.commit()

    answered = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers())
    )
    new_id = answered.json()["draft"]["draft_id"]

    refused = client.post(
        f"/api/v1/ai/strategy/drafts/{new_id}/compile", json={"strategy_id": strategy.id}
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "draft_not_confirmed"


def test_after_a_fresh_confirmation_the_answered_draft_compiles(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload(), confirmed=True)
    strategy = Strategy(name="Answered target", slug="answered-target-2")
    db_session.add(strategy)
    db_session.commit()
    answered = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers())
    )
    new_id = answered.json()["draft"]["draft_id"]

    confirmed = client.post(
        f"/api/v1/ai/strategy/drafts/{new_id}/confirmations",
        json={"decision": "confirmed"},
    )
    compiled = client.post(
        f"/api/v1/ai/strategy/drafts/{new_id}/compile", json={"strategy_id": strategy.id}
    )

    assert confirmed.status_code == 201
    assert compiled.status_code == 201
    assert compiled.json()["result"] == "COMPILED"
    db_session.expire_all()
    row = db_session.get(StrategyDraft, new_id)
    assert row.compiled_strategy_version_id == compiled.json()["strategy_version_id"]


def test_a_stale_hash_is_a_conflict_that_writes_nothing(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload())
    before = _count(db_session, AuditLog)

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json={
            "answered_draft_hash": "0" * 64,
            "answers": [
                {
                    "code": "missing_required_slot",
                    "slot": "risk",
                    "value": {"max_position_pct": 0.5},
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "draft_hash_mismatch"
    assert len(_draft_rows(db_session, run.id)) == 1
    assert _count(db_session, AuditLog) == before
    assert _count(db_session, StrategyVersion) == 0


def test_an_older_draft_cannot_be_answered_once_a_newer_one_exists(client, db_session) -> None:
    """A second browser tab still holding the old form must not fork the lineage."""

    run, draft = _store(db_session, _open_payload())
    # Leave one question open so the new draft can be answered again below.
    first = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers()[:-1])
    )
    assert first.status_code == 201
    current_id = first.json()["draft"]["draft_id"]
    assert [item["slot"] for item in first.json()["remaining_questions"]] == [
        "execution.slippage_bps"
    ]
    before_audit = _count(db_session, AuditLog)
    old_hash = draft_answers.draft_hash_of(_open_payload())

    # The old draft's own bytes never change, so its hash still matches; the only
    # thing that can refuse this is "the run has already moved on".
    again = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers())
    )

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "draft_superseded"
    assert again.json()["error"]["details"] == {
        "draft_id": draft.id,
        "current_draft_id": current_id,
    }
    assert [row.id for row in _draft_rows(db_session, run.id)] == [draft.id, current_id]
    assert _count(db_session, AuditLog) == before_audit
    db_session.expire_all()
    assert run.draft_id == current_id
    assert draft.draft_json == _open_payload()
    assert draft_answers.draft_hash_of(draft.draft_json) == old_hash

    # The guard only refuses the *old* draft: the current one still stacks a version.
    new_row = db_session.get(StrategyDraft, current_id)
    second = client.post(
        ANSWERS_URL.format(draft_id=current_id),
        json=_answers(new_row, ("missing_required_slot", "execution.slippage_bps", 5)),
    )

    assert second.status_code == 201
    assert second.json()["previous_draft_id"] == current_id
    assert second.json()["draft"]["version"] == "3.0"
    assert [row.id for row in _draft_rows(db_session, run.id)] == [
        draft.id,
        current_id,
        second.json()["draft"]["draft_id"],
    ]


def test_a_compiled_draft_cannot_be_answered_through_this_endpoint(client, db_session) -> None:
    run, draft = _store(db_session, _complete_payload(), confirmed=True)
    strategy = Strategy(name="Compiled target", slug="compiled-target")
    db_session.add(strategy)
    db_session.commit()
    compiled = client.post(
        f"/api/v1/ai/strategy/drafts/{draft.id}/compile", json={"strategy_id": strategy.id}
    )
    assert compiled.status_code == 201
    before = _count(db_session, AuditLog)

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers())
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "draft_already_compiled"
    assert len(_draft_rows(db_session, run.id)) == 1
    assert _count(db_session, AuditLog) == before


def test_illegal_answers_are_refused_before_anything_is_written(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload())
    before = _count(db_session, AuditLog)

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json=_answers(
            draft,
            *_open_answers()[:-2],
            ("missing_required_slot", "execution.fee_bps", 2000),
        ),
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "answer_refused"
    assert [problem["code"] for problem in body["details"]["problems"]] == ["answer_invalid"]
    assert len(_draft_rows(db_session, run.id)) == 1
    assert _count(db_session, AuditLog) == before


def test_an_answer_the_compiler_did_not_ask_is_refused(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload())

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json=_answers(draft, ("missing_required_slot", "risk.take_profit_r_multiple", 2.0)),
    )

    assert response.status_code == 422
    problems = response.json()["error"]["details"]["problems"]
    assert [problem["code"] for problem in problems] == ["answer_not_a_question"]


def test_partial_answers_keep_the_rest_of_the_questions_open(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload())

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json=_answers(
            draft,
            (
                "missing_required_slot",
                "risk",
                {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
            ),
        ),
    )

    assert response.status_code == 201
    body = response.json()
    remaining = {question["slot"] for question in body["remaining_questions"]}
    assert "risk" not in remaining
    assert {"market.timeframes", "exit.long", "sizing", "sizing.mode"} <= remaining
    new_id = body["draft"]["draft_id"]
    listed = client.get(QUESTIONS_URL.format(draft_id=new_id))
    assert listed.status_code == 200
    assert {question["slot"] for question in listed.json()["questions"]} == remaining
    # The questions endpoint answers about the draft the run now points at, so the
    # hash it reports is the new draft's -- not the compile report of the old one.
    assert listed.json()["draft_hash"] == draft_answers.draft_hash_of(body["draft"]["content"])
    assert listed.json()["result"] == "NEEDS_USER_DECISION"


def test_an_answer_that_fails_the_research_gate_writes_nothing(client, db_session) -> None:
    # The hypothesis states two EXPLICIT risk rules, so the draft's single
    # field-level unknown for "risk" cannot answer for both of them and the gate
    # refuses the payload -- which is a failure only the gate can see.
    run, draft = _store(db_session, _open_payload(), hypothesis=_hypothesis(risk_rules=2))
    before = _count(db_session, AuditLog)

    response = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json=_answers(
            draft,
            (
                "missing_required_slot",
                "risk",
                {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
            ),
        ),
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "answer_failed_the_gates"
    codes = {violation["code"] for violation in body["details"]["violations"]}
    assert "dropped_explicit_rule" in codes
    assert len(_draft_rows(db_session, run.id)) == 1
    assert _count(db_session, AuditLog) == before


def test_the_audit_event_links_both_drafts_and_every_answer(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload())
    entries = _open_answers()

    response = client.post(ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *entries))
    new_id = response.json()["draft"]["draft_id"]

    events = _audit_rows(db_session)
    assert len(events) == 1
    event = events[0]
    assert event.entity_type == "strategy_draft"
    assert event.entity_id == str(new_id)
    assert event.action == "draft_answers_applied"
    payload = event.payload_json
    assert payload["previous_draft_id"] == draft.id
    assert payload["draft_id"] == new_id
    assert payload["previous_draft_hash"] == draft_answers.draft_hash_of(draft.draft_json)
    assert payload["run_id"] == run.id
    assert payload["hypothesis_id"] == draft.hypothesis_id
    assert payload["is_human_decision"] is True
    assert payload["decided_by"] == "user"
    assert payload["strategy_version_created"] is False
    assert payload["answered_at"]
    assert [(item["code"], item["slot"]) for item in payload["answers"]] == [
        (code, slot) for code, slot, _value in entries
    ]
    assert [item["slot"] for item in payload["applied"]] == [
        slot for _code, slot, _value in entries
    ]
    # Every applied value that becomes a rule names the rule; an indicator input and
    # a timeframe patch an existing declaration instead of inventing one.
    rule_backed = [
        item
        for item in payload["applied"]
        if item["slot"] != "market.timeframes" and not item["slot"].startswith("indicators.")
    ]
    assert rule_backed and all(item["rule_id"] for item in rule_backed)
    new_row = db_session.get(StrategyDraft, new_id)
    assert any(rule["id"].startswith("human-") for rule in new_row.draft_json["rules"])
    assert payload["draft_hash"] != payload["previous_draft_hash"]


def test_the_audit_event_is_the_only_record_the_old_draft_keeps(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload(), confirmed=True)
    old_events = [
        row.id
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.entity_id == str(draft.id))
        ).all()
    ]
    assert len(old_events) == 1  # the confirmation the person gave the old draft

    answered = client.post(
        ANSWERS_URL.format(draft_id=draft.id), json=_answers(draft, *_open_answers())
    )
    new_id = answered.json()["draft"]["draft_id"]

    # The answers event is filed against the new draft; the old draft's own record
    # (its confirmation) is neither repeated nor rewritten, and re-confirming the old
    # draft could not have authorized the new one -- the new draft is unconfirmed.
    still = [
        row.id
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.entity_id == str(draft.id))
        ).all()
    ]
    assert still == old_events
    assert [row.entity_id for row in _audit_rows(db_session)] == [str(new_id)]
    assert (
        client.get(QUESTIONS_URL.format(draft_id=new_id)).json()["compiled_strategy_version_id"]
        is None
    )
    assert db_session.get(StrategyDraft, new_id).draft_json != draft.draft_json


def test_a_missing_draft_is_a_404_on_both_endpoints(client) -> None:
    assert client.get(QUESTIONS_URL.format(draft_id=999_999)).status_code == 404
    assert (
        client.post(
            ANSWERS_URL.format(draft_id=999_999),
            json={"answered_draft_hash": "x", "answers": [{"code": "c", "slot": "s", "value": 1}]},
        ).status_code
        == 404
    )


def test_the_request_schema_refuses_extra_fields_and_empty_batches(client, db_session) -> None:
    run, draft = _store(db_session, _open_payload())

    extra = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json={
            "answered_draft_hash": draft_answers.draft_hash_of(draft.draft_json),
            "answers": [{"code": "unknown_blocks_slot", "slot": "risk", "value": 1, "dsl": {}}],
        },
    )
    empty = client.post(
        ANSWERS_URL.format(draft_id=draft.id),
        json={"answered_draft_hash": draft_answers.draft_hash_of(draft.draft_json), "answers": []},
    )
    # A client may not hand the server a spec, a rule or a slot of its own choosing.
    assert extra.status_code == 422
    assert empty.status_code == 422


def test_the_questions_endpoint_reports_the_hash_and_the_counts(client, db_session) -> None:
    run, draft = _store(db_session, _short_payload())

    response = client.get(QUESTIONS_URL.format(draft_id=draft.id))

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == draft.id
    assert body["version"] == "1.0"
    assert body["result"] == "NEEDS_USER_DECISION"
    assert body["draft_hash"] == draft_answers.draft_hash_of(_short_payload())
    assert body["answer_schema_version"] == draft_answers.ANSWER_SCHEMA_VERSION
    assert body["compiled_strategy_version_id"] is None
    by_slot = {question["slot"]: question for question in body["questions"]}
    assert by_slot["market.allow_short"]["answerable"] is False
    assert by_slot["market.timeframes"]["kind"] == "timeframe"
    assert by_slot["market.timeframes"]["choices"]
    assert by_slot["risk"]["keys"][0]["kind"] == "number"
    assert body["answerable_count"] + body["unanswerable_count"] == len(body["questions"])
    assert body["unanswerable_count"] >= 1


def test_the_answer_path_has_no_dialect_specific_sql() -> None:
    """A guard, not a proof: the CI run against PostgreSQL is the proof (ADR-211).

    The answer path must stay portable -- one ``JSON`` column, one transaction, no
    ``text()``, no dialect branch -- so SQLite (local UAT, the guarded suite) and
    PostgreSQL (production) cannot diverge.
    """

    from pathlib import Path

    import app.ai.draft_answers as module

    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "postgresql" not in source
    assert "sqlite" not in source
    assert 'text("' not in source
    assert "on_conflict" not in source
