"""Structured answers to the compiler's user-decidable questions (ADR-211, gap G7).

The compiler says *what* it cannot decide (docs/29 §12, §16.6) and refuses to fill
it in. Before v2.7.0 the only channel back was the free-text note on a human
confirmation -- and the architect prompt never reads it, so a draft stuck in
``NEEDS_USER_DECISION`` stayed stuck forever.

This module closes that loop **without touching the compiler**:

1. the report is recomputed from the stored draft (the compiler is the only source
   of questions -- a client cannot invent one);
2. every answer is checked against that report and against the vocabulary the
   compiler itself uses (``app.compiler.mapping``), never trusted as DSL;
3. the answers are applied to a *copy* of the draft payload by
   :func:`apply_answers`, a pure function;
4. the result must pass the same research gate an architect draft passes
   (``app.ai.research.evaluate_draft_payload``);
5. the result is stored as a **new** draft row of the same run (``version = N+1.0``
   -- the mechanism ``_store_draft`` already uses), and one ``audit_logs`` event
   links the old draft, the new draft and every answer.

Nothing here calls AI. The value came from a person, so the new row records
``ai_task_id=None`` and the audit says ``is_human_decision``.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.compiler.errors import USER_DECIDABLE_CODES
from app.compiler.mapping import (
    ORDER_TYPES,
    RISK_KEYS,
    SIZING_KEYS,
    SIZING_MODES,
    SYSTEM_TIMEFRAME_SLOT,
    parse_canonical_condition,
    read_declaration,
)
from app.research.metrics import BARRS_PER_YEAR

__all__ = [
    "ANSWER_SCHEMA_VERSION",
    "Answer",
    "AnswerOutcome",
    "AnswerProblem",
    "Applied",
    "Question",
    "QuestionKey",
    "Refused",
    "answer_draft_questions",
    "apply_answers",
    "build_questions",
    "report_for_questions",
]

#: Bumped when the answer vocabulary changes shape (an older stored draft still
#: answers, but a client should not cache a question list across versions).
ANSWER_SCHEMA_VERSION = "1.0"

#: The compile target used to *ask* the questions. The questions a draft raises do
#: not depend on which strategy version it will become (docs/29 §8), and this path
#: must never look like a compile of a real target, so the neutral one is used.
QUESTION_TARGET_ID = 0
QUESTION_TARGET_VERSION = "0"

#: Slots the compiler asks about that no draft edit can settle. Each one names the
#: reason: a form that silently drops a question would be a lie (ADR-138).
UNSUPPORTED_SLOTS: dict[str, str] = {
    "market.allow_short": (
        "编译器要求「允许做空」，但草案格式里没有承载这个答案的字段——编译器的规范里"
        "根本不读 market.allow_short（它只在策略 DSL 里存在，草案从来没有它）。本版不会"
        "替你猜一个值，也不会伪造一个能骗过编译器的字段；要有做空支持得先改编译器契约"
        "（docs/29 §12）。"
    ),
}

_UNSUPPORTED_GENERIC = (
    "这条不是「由人给一个值」能解决的：编译器给出的这一类问题属于结构性拒绝"
    "（比如规则没映射、参数不合法、能力缺失、规则互相冲突）。要改就必须回去改假设和"
    "草案内容，本版不允许绕过。"
)


# --------------------------------------------------------------------------- #
# What a question can be answered with
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NumberSpec:
    """One numeric slot, with the bounds the strategy DSL itself enforces."""

    label: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    integer: bool = False


@dataclass(frozen=True)
class EnumSpec:
    label: str
    choices: tuple[str, ...]
    choice_labels: Mapping[str, str] = field(default_factory=dict)


_NUMBER_SLOTS: dict[str, NumberSpec] = {
    "risk.stop_loss_atr_multiple": NumberSpec(
        "止损（ATR 倍数）", "倍 ATR", minimum=0, exclusive_minimum=True
    ),
    "risk.take_profit_r_multiple": NumberSpec(
        "止盈（R 倍数）", "倍 R", minimum=0, exclusive_minimum=True
    ),
    "risk.take_profit_atr_multiple": NumberSpec(
        "止盈（ATR 倍数）", "倍 ATR", minimum=0, exclusive_minimum=True
    ),
    "risk.max_position_pct": NumberSpec(
        "单笔最大仓位", "占账户的比例", minimum=0, maximum=1, exclusive_minimum=True
    ),
    "sizing.fraction": NumberSpec(
        "每次投入的账户比例", "占账户的比例", minimum=0, maximum=1, exclusive_minimum=True
    ),
    "sizing.risk_pct": NumberSpec(
        "每笔愿意承担的风险", "占账户的比例", minimum=0, maximum=1, exclusive_minimum=True
    ),
    "sizing.atr_multiple": NumberSpec(
        "风险距离（ATR 倍数）", "倍 ATR", minimum=0, exclusive_minimum=True
    ),
    "execution.fee_bps": NumberSpec("手续费", "基点（1bp = 万分之一）", minimum=0, maximum=1000),
    "execution.slippage_bps": NumberSpec("滑点", "基点（1bp = 万分之一）", minimum=0, maximum=1000),
    "execution.order_valid_bars": NumberSpec(
        "挂单有效 K 线数", "根", minimum=1, maximum=100, integer=True
    ),
    "execution.limit_offset_atr": NumberSpec("限价偏移", "倍 ATR", minimum=0),
    "execution.stop_offset_atr": NumberSpec("触发价偏移", "倍 ATR", minimum=0),
}

_ENUM_SLOTS: dict[str, EnumSpec] = {
    "sizing.mode": EnumSpec(
        "仓位怎么定",
        SIZING_MODES,
        {
            "fixed_fraction": "固定比例：每次用一部分现金",
            "risk_per_trade": "按单笔风险：先定愿意亏多少",
            "atr_risk": "按 ATR 距离：用波动幅度定仓位",
        },
    ),
    "execution.entry_order_type": EnumSpec(
        "入场用什么单",
        ORDER_TYPES,
        {"market": "市价单（下一根开盘价成交）", "limit": "限价单", "stop": "触价单（stop）"},
    ),
}

#: Blocks the compiler asks about as a whole: the question is "this block says
#: nothing", and the answer may fill several keys at once.
_BLOCK_SLOTS: dict[str, tuple[str, ...]] = {"risk": RISK_KEYS, "sizing": SIZING_KEYS}

_CONDITION_SLOTS = {
    "entry.long": ("entry", "long"),
    "entry.short": ("entry", "short"),
    "exit.long": ("exit", "long"),
    "exit.short": ("exit", "short"),
}

_RISK_SLOT_PREFIX = "risk."
_SIZING_SLOT_PREFIX = "sizing."
_EXECUTION_SLOT_PREFIX = "execution."
_INDICATOR_SLOT_PREFIX = "indicators."

_NUMERIC_FIELDS = ("risk", "sizing", "execution")


@dataclass(frozen=True)
class QuestionKey:
    """One answerable key of a block answer (``risk`` / ``sizing``)."""

    name: str
    kind: str
    label: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    integer: bool = False
    choices: tuple[str, ...] = ()
    choice_labels: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "exclusive_minimum": self.exclusive_minimum,
            "integer": self.integer,
            "choices": list(self.choices),
            "choice_labels": dict(self.choice_labels),
        }


@dataclass(frozen=True)
class Question:
    """One compiler question a person can answer."""

    code: str
    slot: str
    kind: str  # number | enum | text | timeframe | condition | block | unsupported
    label: str
    detail: str = ""
    rule_ids: tuple[str, ...] = ()
    #: Every rejection code that asked about this slot. The compiler can ask the
    #: same thing twice -- ``unknown_blocks_slot`` because the draft admits the
    #: material never said it, and ``missing_required_slot`` because the block it
    #: would have carried is empty -- and a person answers that *once*.
    codes: tuple[str, ...] = ()
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    integer: bool = False
    choices: tuple[str, ...] = ()
    choice_labels: Mapping[str, str] = field(default_factory=dict)
    keys: tuple[QuestionKey, ...] = ()
    answerable: bool = True
    value_optional: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "slot": self.slot,
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
            "rule_ids": list(self.rule_ids),
            "codes": list(self.codes),
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "exclusive_minimum": self.exclusive_minimum,
            "integer": self.integer,
            "choices": list(self.choices),
            "choice_labels": dict(self.choice_labels),
            "keys": [item.to_dict() for item in self.keys],
            "answerable": self.answerable,
            "value_optional": self.value_optional,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Answer:
    """One answer as it arrives from a client, before any validation."""

    code: str
    slot: str
    value: Any = None


@dataclass(frozen=True)
class Applied:
    """One answer that was actually written into the new draft."""

    code: str
    slot: str
    value: Any
    label: str
    summary: str
    rule_id: str | None = None
    unknown_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "slot": self.slot,
            "value": self.value,
            "label": self.label,
            "summary": self.summary,
            "rule_id": self.rule_id,
            "unknown_fields": list(self.unknown_fields),
        }


@dataclass(frozen=True)
class AnswerProblem:
    """Why one answer was refused. The payload is never half-written."""

    code: str
    slot: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "slot": self.slot, "message": self.message}


@dataclass(frozen=True)
class AnswerOutcome:
    """The result of applying answers: a whole new payload, or only problems."""

    payload: dict[str, Any]
    applied: tuple[Applied, ...] = ()
    problems: tuple[AnswerProblem, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


class Refused(Exception):
    """A refusal of the answers endpoint, carrying the HTTP outcome."""

    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


# --------------------------------------------------------------------------- #
# Reading the questions out of a compile report
# --------------------------------------------------------------------------- #
def _declarations(payload: Mapping[str, Any]) -> dict[str, int]:
    """``{slot_name: index}`` for every indicator the draft declares.

    Only a declared indicator can be edited by an answer: inventing a declaration
    the draft never made would be the compiler's job, not a person's (docs/29 §19).
    """

    raw_parameters = payload.get("parameters")
    parameters = raw_parameters if isinstance(raw_parameters, Mapping) else {}
    found: dict[str, int] = {}
    indicators = payload.get("indicators")
    if not isinstance(indicators, Sequence) or isinstance(indicators, (str, bytes)):
        return found
    for index, item in enumerate(indicators):
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        params = item.get("parameters")
        declaration = read_declaration(
            name.strip(),
            params if isinstance(params, Mapping) else {},
            parameters,
            strict_keys=False,
        )
        found[declaration.slot_name()] = index
    return found


def _existing_parameter_keys(payload: Mapping[str, Any], field_name: str) -> set[str]:
    keys: set[str] = set()
    rules = payload.get("rules")
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        return keys
    for rule in rules:
        if not isinstance(rule, Mapping) or rule.get("field") != field_name:
            continue
        params = rule.get("parameters")
        if isinstance(params, Mapping):
            keys.update(str(key) for key in params)
    return keys


def _rule_owner(payload: Mapping[str, Any], field_name: str, key: str) -> str | None:
    rules = payload.get("rules")
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        return None
    for rule in rules:
        if not isinstance(rule, Mapping) or rule.get("field") != field_name:
            continue
        params = rule.get("parameters")
        if isinstance(params, Mapping) and key in params:
            return str(rule.get("id") or "?")
    return None


def _new_rule_id(payload: Mapping[str, Any], field_name: str) -> str:
    used: set[str] = set()
    rules = payload.get("rules")
    if isinstance(rules, Sequence) and not isinstance(rules, (str, bytes)):
        for rule in rules:
            if isinstance(rule, Mapping) and rule.get("id") is not None:
                used.add(str(rule.get("id")))
    index = 0
    while True:
        index += 1
        candidate = f"human-{field_name}-{index}"
        if candidate not in used:
            return candidate


def _placed_condition(payload: Mapping[str, Any], field_name: str) -> bool:
    rules = payload.get("rules")
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        return False
    for rule in rules:
        if not isinstance(rule, Mapping) or rule.get("field") != field_name:
            continue
        params = rule.get("parameters")
        if isinstance(params, Mapping) and {"left", "operator", "right"} <= set(params):
            return True
    return False


def _key_for(slot: str) -> QuestionKey | None:
    """The answerable descriptor of one sub-key of a block, or ``None``.

    ``slot`` is the full compiler slot (``risk.max_position_pct``); the key's
    ``name`` is what a client writes *inside* the block answer, because the block
    is the slot -- ``{"max_position_pct": 0.5}``, not the slot name repeated.
    """

    if slot in _ENUM_SLOTS:
        spec = _ENUM_SLOTS[slot]
        return QuestionKey(
            name=slot.rsplit(".", 1)[-1],
            kind="enum",
            label=spec.label,
            choices=spec.choices,
            choice_labels=dict(spec.choice_labels),
        )
    if slot in _NUMBER_SLOTS:
        spec = _NUMBER_SLOTS[slot]
        return QuestionKey(
            name=slot.rsplit(".", 1)[-1],
            kind="number",
            label=spec.label,
            unit=spec.unit,
            minimum=spec.minimum,
            maximum=spec.maximum,
            exclusive_minimum=spec.exclusive_minimum,
            integer=spec.integer,
        )
    return None


def _unsupported(code: str, slot: str, detail: str, rule_ids, reason: str) -> Question:
    return Question(
        code=code,
        slot=slot,
        kind="unsupported",
        label="这一条本版不能由你直接回答",
        detail=detail,
        rule_ids=rule_ids,
        answerable=False,
        reason=reason,
    )


def question_for(
    item: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    declarations: Mapping[str, int] | None = None,
) -> Question:
    """Turn one report rejection into the question (and control) it deserves."""

    code = str(item.get("code") or "")
    slot = str(item.get("slot") or "")
    detail = str(item.get("detail") or "")
    rule_ids = tuple(str(value) for value in (item.get("rule_ids") or ()))
    declared = _declarations(payload) if declarations is None else declarations
    optional = code == "unknown_blocks_slot"

    # A structural rejection is never a question, whatever slot it names: a client
    # that hands one in gets a refusal with the reason, not a form (m56025: refuse,
    # never fake).
    if code not in USER_DECIDABLE_CODES:
        return _unsupported(code, slot, detail, rule_ids, _UNSUPPORTED_GENERIC)
    if slot in UNSUPPORTED_SLOTS:
        return _unsupported(code, slot, detail, rule_ids, UNSUPPORTED_SLOTS[slot])
    if slot in _NUMBER_SLOTS:
        spec = _NUMBER_SLOTS[slot]
        return Question(
            code=code,
            slot=slot,
            kind="number",
            label=spec.label,
            detail=detail,
            rule_ids=rule_ids,
            unit=spec.unit,
            minimum=spec.minimum,
            maximum=spec.maximum,
            exclusive_minimum=spec.exclusive_minimum,
            integer=spec.integer,
            value_optional=optional,
        )
    if slot in _ENUM_SLOTS:
        spec = _ENUM_SLOTS[slot]
        return Question(
            code=code,
            slot=slot,
            kind="enum",
            label=spec.label,
            detail=detail,
            rule_ids=rule_ids,
            choices=spec.choices,
            choice_labels=dict(spec.choice_labels),
            value_optional=optional,
        )
    if slot == SYSTEM_TIMEFRAME_SLOT:
        return Question(
            code=code,
            slot=slot,
            kind="timeframe",
            label="用哪个 K 线周期来回测",
            detail=detail,
            rule_ids=rule_ids,
            choices=tuple(BARRS_PER_YEAR),
            choice_labels={name: name for name in BARRS_PER_YEAR},
            value_optional=optional,
        )
    if slot in _BLOCK_SLOTS:
        taken = _existing_parameter_keys(payload, slot)
        keys = tuple(
            descriptor
            for descriptor in (_key_for(f"{slot}.{name}") for name in _BLOCK_SLOTS[slot])
            if descriptor is not None and descriptor.name not in taken
        )
        return Question(
            code=code,
            slot=slot,
            kind="block",
            label=f"「{slot}」这一整块还没有内容",
            detail=detail,
            rule_ids=rule_ids,
            keys=keys,
            value_optional=optional,
        )
    if slot in _CONDITION_SLOTS:
        field_name, side = _CONDITION_SLOTS[slot]
        return Question(
            code=code,
            slot=slot,
            kind="condition",
            label=f"{field_name} 的 {side} 条件（左值、比较、右值）",
            detail=detail,
            rule_ids=rule_ids,
            value_optional=optional,
        )
    if slot.startswith(_INDICATOR_SLOT_PREFIX):
        return _indicator_question(
            code, slot, detail, rule_ids, declared=declared, optional=optional
        )
    return _unsupported(code, slot, detail, rule_ids, _UNSUPPORTED_GENERIC)


def _indicator_question(
    code: str, slot: str, detail: str, rule_ids, *, declared: Mapping[str, int], optional: bool
) -> Question:
    """Period / input of an indicator the draft already declares."""

    bare = slot
    for suffix in (".input", ".period"):
        if bare.endswith(suffix):
            bare = bare[: -len(suffix)]
            break
    if bare not in declared:
        return _unsupported(
            code,
            slot,
            detail,
            rule_ids,
            (
                "草案里没有与这条问题对应的指标声明（规则用到了一个从没声明过的指标名）。"
                "补一条声明是编译器的工作，不是人的答案，所以本版不会替你造一个——请回去把"
                "草案里的指标声明写清楚。"
            ),
        )
    if slot.endswith(".input"):
        return Question(
            code=code,
            slot=slot,
            kind="text",
            label="这条指标算在哪一列上（例如 close）",
            detail=detail,
            rule_ids=rule_ids,
            value_optional=optional,
        )
    return Question(
        code=code,
        slot=slot,
        kind="number",
        label="这条指标用哪个周期",
        detail=detail,
        rule_ids=rule_ids,
        unit="根",
        minimum=1,
        integer=True,
        value_optional=optional,
    )


def build_questions(report: Mapping[str, Any], payload: Mapping[str, Any]) -> list[Question]:
    """Every question in ``report`` a person may answer, one per slot.

    The compiler may report the same slot twice (``unknown_blocks_slot`` plus
    ``missing_required_slot``); that is one thing to decide, so the entries are
    merged and the merged question carries every code that asked for it. An answer
    naming any of those codes is accepted, and a value is required whenever one of
    them was a ``missing_required_slot``.
    """

    declarations = _declarations(payload)
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    rejections = report.get("rejections")
    if not isinstance(rejections, Sequence):
        return []
    for item in rejections:
        if not isinstance(item, Mapping) or not item.get("user_decidable"):
            continue
        grouped.setdefault(str(item.get("slot") or ""), []).append(item)

    questions: list[Question] = []
    for _slot, items in grouped.items():
        strict = [item for item in items if str(item.get("code") or "") != "unknown_blocks_slot"]
        primary = strict[0] if strict else items[0]
        question = question_for(primary, payload=payload, declarations=declarations)
        detail = next(
            (str(item.get("detail") or "") for item in strict if item.get("detail")),
            str(items[0].get("detail") or ""),
        )
        questions.append(
            dataclasses.replace(
                question,
                code=str(primary.get("code") or ""),
                codes=tuple(dict.fromkeys(str(item.get("code") or "") for item in items)),
                rule_ids=tuple(
                    dict.fromkeys(
                        str(rule_id) for item in items for rule_id in (item.get("rule_ids") or ())
                    )
                ),
                detail=detail,
                value_optional=not strict,
            )
        )
    return questions


# --------------------------------------------------------------------------- #
# Applying the answers
# --------------------------------------------------------------------------- #
def _number_of(value: Any, *, label: str, spec: NumberSpec) -> tuple[float | None, str]:
    """A finite number inside the bounds the DSL itself enforces."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"「{label}」要填一个数字。"
    number = float(value)
    if not math.isfinite(number):
        return None, f"「{label}」要填一个有限的数字。"
    if spec.integer and number != int(number):
        return None, f"「{label}」要填整数。"
    if spec.minimum is not None:
        if spec.exclusive_minimum and number <= spec.minimum:
            return None, f"「{label}」必须大于 {spec.minimum:g}。"
        if not spec.exclusive_minimum and number < spec.minimum:
            return None, f"「{label}」不能小于 {spec.minimum:g}。"
    if spec.maximum is not None and number > spec.maximum:
        return None, f"「{label}」不能大于 {spec.maximum:g}。"
    return (float(int(number)) if spec.integer else number), ""


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, Mapping):
        return "、".join(f"{key}={_render(item)}" for key, item in value.items())
    return str(value)


def _unknown_fields_for(slot: str) -> tuple[str, ...]:
    """Which ``draft.unknowns`` entries an answer to ``slot`` settles.

    ``compiler.py`` reads those entries *before* it reads the risk/sizing values,
    so writing a value alone would leave ``unknown_blocks_slot`` in the report
    forever. The entry is kept (a field-level unknown is what tells
    ``validate_draft`` the material never stated this), only its
    ``needed_to_formalize`` flag is cleared -- that is the person saying "I decided
    this; the draft no longer has to ask".
    """

    if slot == SYSTEM_TIMEFRAME_SLOT:
        return ("timeframe",)
    if slot in _CONDITION_SLOTS:
        return (_CONDITION_SLOTS[slot][0],)
    for prefix, name in (
        (_RISK_SLOT_PREFIX, "risk"),
        (_SIZING_SLOT_PREFIX, "sizing"),
        (_EXECUTION_SLOT_PREFIX, "execution"),
    ):
        if slot == name or slot.startswith(prefix):
            return (name,)
    return ()


def _clear_unknowns(payload: dict[str, Any], fields: set[str]) -> None:
    unknowns = payload.get("unknowns")
    if not isinstance(unknowns, list):
        return
    for unknown in unknowns:
        if isinstance(unknown, dict) and str(unknown.get("field") or "") in fields:
            unknown["needed_to_formalize"] = False


def _disclose_assumptions(payload: dict[str, Any], fields: Sequence[str]) -> None:
    """Say, in the draft itself, that a person filled these fields in.

    ``validate_draft`` refuses an ASSUMED rule that no assumption names
    (``assumed_not_disclosed``), and the vocabulary is ``rules[].field``, never a
    rule id (ADR-154). Only the fields this answering run wrote are disclosed: a
    draft that arrived with an undisclosed assumption keeps that violation instead
    of having it quietly repaired by an unrelated answer.
    """

    if not fields:
        return
    assumptions = payload.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = []
        payload["assumptions"] = assumptions
    disclosed = {
        str(name)
        for item in assumptions
        if isinstance(item, Mapping)
        for name in (item.get("applies_to") or ())
    }
    for name in fields:
        if name in disclosed:
            continue
        assumptions.append(
            {
                "statement": f"人工回答：{name} 这一项由操作人给出，不是 AI 推的。",
                "applies_to": [name],
                "reason": "编译器把这一项列为必须由人来定的问题（ADR-211）。",
            }
        )
        disclosed.add(name)


def _append_rule(
    payload: dict[str, Any], *, field_name: str, parameters: dict[str, Any], statement: str
) -> str:
    rule_id = _new_rule_id(payload, field_name)
    rules = payload.get("rules")
    if not isinstance(rules, list):
        rules = []
        payload["rules"] = rules
    rules.append(
        {
            "id": rule_id,
            "field": field_name,
            "statement": statement,
            "origin": "ASSUMED",
            "confidence": "high",
            "derived_from": None,
            "evidence": [],
            "parameters": parameters,
            "required_capabilities": [],
            "note": "这一条由人在「需要你先做决定」表单里作答后写入（ADR-211），不是模型写的。",
        }
    )
    return rule_id


def _split_slot(slot: str) -> tuple[str, str]:
    """``("sizing.fraction") -> ("sizing", "fraction")``; ``("", slot)`` otherwise."""

    for prefix, field_name in (
        (_RISK_SLOT_PREFIX, "risk"),
        (_SIZING_SLOT_PREFIX, "sizing"),
        (_EXECUTION_SLOT_PREFIX, "execution"),
    ):
        if slot.startswith(prefix):
            return field_name, slot[len(prefix) :]
    return "", slot


def _indicator_index(payload: Mapping[str, Any], slot: str) -> int | None:
    bare = slot
    for suffix in (".input", ".period"):
        if bare.endswith(suffix):
            bare = bare[: -len(suffix)]
            break
    return _declarations(payload).get(bare)


def _absorb(
    answer: Answer,
    question: Question,
    *,
    payload: Mapping[str, Any],
    field_values: dict[str, dict[str, Any]],
    indicator_edits: dict[int, dict[str, Any]],
    condition_values: dict[tuple[str, str], dict[str, Any]],
) -> tuple[AnswerProblem | None, Any]:
    """Validate one answer and file it under the edit it will make.

    Returns ``(problem, value)``: on success the problem is ``None`` and the value
    is the *normalised* one that will be written (and recorded).
    """

    slot = answer.slot
    if question.kind == "number" and slot.startswith(_INDICATOR_SLOT_PREFIX):
        period, message = _number_of(
            answer.value, label=question.label, spec=NumberSpec("周期", minimum=1, integer=True)
        )
        if period is None:
            return AnswerProblem("answer_invalid", slot, message), None
        target = _indicator_index(payload, slot)
        if target is None:
            return (
                AnswerProblem(
                    "answer_not_supported", slot, "草案里已经没有这条指标声明了，答案没法落地。"
                ),
                None,
            )
        indicator_edits.setdefault(target, {})["period"] = int(period)
        return None, int(period)

    if question.kind == "number":
        field_name, name = _split_slot(slot)
        spec = _NUMBER_SLOTS.get(slot)
        if spec is None or not field_name:
            return AnswerProblem("answer_not_supported", slot, _UNSUPPORTED_GENERIC), None
        number, message = _number_of(answer.value, label=spec.label, spec=spec)
        if number is None:
            return AnswerProblem("answer_invalid", slot, message), None
        owner = _rule_owner(payload, field_name, name)
        if owner is not None:
            return (
                AnswerProblem(
                    "answer_conflict",
                    slot,
                    f"草案里的规则「{owner}」已经写了「{spec.label}」；再给一个值会让编译器报"
                    "规则冲突，所以这一条请回去改草案。",
                ),
                None,
            )
        field_values.setdefault(field_name, {})[name] = number
        return None, number

    if question.kind == "enum":
        if not isinstance(answer.value, str) or answer.value not in question.choices:
            return (
                AnswerProblem(
                    "answer_invalid",
                    slot,
                    f"「{question.label}」只能填以下之一：{'、'.join(question.choices)}。",
                ),
                None,
            )
        field_name, name = _split_slot(slot)
        if not field_name:
            return AnswerProblem("answer_not_supported", slot, _UNSUPPORTED_GENERIC), None
        owner = _rule_owner(payload, field_name, name)
        if owner is not None:
            return (
                AnswerProblem(
                    "answer_conflict",
                    slot,
                    f"草案里的规则「{owner}」已经写了「{question.label}」；请回去改草案。",
                ),
                None,
            )
        field_values.setdefault(field_name, {})[name] = answer.value
        return None, answer.value

    if question.kind == "text":
        text = answer.value.strip() if isinstance(answer.value, str) else ""
        if not text or len(text) > 64 or "\n" in text:
            return (
                AnswerProblem(
                    "answer_invalid",
                    slot,
                    f"「{question.label}」要填一个列名（1 到 64 个字符，不能换行）。",
                ),
                None,
            )
        target = _indicator_index(payload, slot)
        if target is None:
            return (
                AnswerProblem(
                    "answer_not_supported", slot, "草案里已经没有这条指标声明了，答案没法落地。"
                ),
                None,
            )
        indicator_edits.setdefault(target, {})["input"] = text
        return None, text

    if question.kind == "timeframe":
        value = answer.value.strip() if isinstance(answer.value, str) else ""
        if value not in BARRS_PER_YEAR:
            return (
                AnswerProblem(
                    "answer_invalid",
                    slot,
                    f"「{question.label}」只能填以下之一：{'、'.join(BARRS_PER_YEAR)}。",
                ),
                None,
            )
        return None, value

    if question.kind == "condition":
        field_name, side = _CONDITION_SLOTS[slot]
        if _placed_condition(payload, field_name):
            return (
                AnswerProblem(
                    "answer_conflict",
                    slot,
                    f"草案里已经有一条 {field_name} 条件了；再加一条需要先定「几条条件怎么合并」"
                    "（all / any），本版不猜这个，请回去改草案。",
                ),
                None,
            )
        if not isinstance(answer.value, Mapping):
            return (
                AnswerProblem(
                    "answer_invalid", slot, "这条条件要填 left、operator、right（可选 period）。"
                ),
                None,
            )
        extra = set(answer.value) - {"left", "operator", "right", "period"}
        if extra:
            return (
                AnswerProblem(
                    "answer_invalid",
                    slot,
                    "这条条件只接受 left、operator、right、period，多了："
                    + "、".join(sorted(str(name) for name in extra))
                    + "。",
                ),
                None,
            )
        parameters: dict[str, Any] = {
            "left": answer.value.get("left"),
            "operator": answer.value.get("operator"),
            "right": answer.value.get("right"),
        }
        if answer.value.get("period") is not None:
            parameters["period"] = answer.value.get("period")
        if (
            parameters["left"] is None
            or parameters["operator"] is None
            or parameters["right"] is None
        ):
            return (
                AnswerProblem(
                    "answer_invalid", slot, "这条条件缺了 left、operator 或 right 中的一个。"
                ),
                None,
            )
        condition, issue = parse_canonical_condition(parameters)
        if issue is not None or condition is None:
            return (
                AnswerProblem(
                    "answer_invalid",
                    slot,
                    "这不是编译器认得的条件写法："
                    + (issue.detail if issue is not None else "无法解析"),
                ),
                None,
            )
        parameters["side"] = side
        condition_values[(field_name, side)] = parameters
        return None, dict(answer.value)

    if question.kind == "block":
        if not isinstance(answer.value, Mapping):
            return AnswerProblem(
                "answer_invalid", slot, "这一块要填一个「字段名：值」的对象。"
            ), None
        allowed = {item.name for item in question.keys}
        unknown = {str(name) for name in answer.value} - allowed
        if unknown:
            return (
                AnswerProblem(
                    "answer_invalid",
                    slot,
                    "这一块不接受这些字段："
                    + "、".join(sorted(unknown))
                    + f"。只能填：{'、'.join(sorted(allowed))}。",
                ),
                None,
            )
        if not answer.value:
            return AnswerProblem("answer_invalid", slot, "这一块至少要填一个字段。"), None
        accepted: dict[str, Any] = {}
        for name, raw in answer.value.items():
            descriptor = _key_for(f"{slot}.{name}")
            if descriptor is None:
                return (
                    AnswerProblem("answer_not_supported", f"{slot}.{name}", _UNSUPPORTED_GENERIC),
                    None,
                )
            if descriptor.kind == "enum":
                if not isinstance(raw, str) or raw not in descriptor.choices:
                    return (
                        AnswerProblem(
                            "answer_invalid",
                            f"{slot}.{name}",
                            f"「{descriptor.label}」只能填以下之一："
                            + "、".join(descriptor.choices)
                            + "。",
                        ),
                        None,
                    )
                accepted[name] = raw
                continue
            number, message = _number_of(
                raw, label=descriptor.label, spec=_NUMBER_SLOTS[f"{slot}.{name}"]
            )
            if number is None:
                return AnswerProblem("answer_invalid", f"{slot}.{name}", message), None
            owner = _rule_owner(payload, slot, str(name))
            if owner is not None:
                return (
                    AnswerProblem(
                        "answer_conflict",
                        f"{slot}.{name}",
                        f"草案里的规则「{owner}」已经写了「{descriptor.label}」；请回去改草案。",
                    ),
                    None,
                )
            accepted[str(name)] = number
        field_values.setdefault(slot, {}).update(accepted)
        return None, accepted

    return AnswerProblem("answer_not_supported", slot, _UNSUPPORTED_GENERIC), None


def apply_answers(
    payload: Mapping[str, Any], answers: Sequence[Answer], *, questions: Sequence[Question]
) -> AnswerOutcome:
    """Apply validated answers to a copy of the draft payload.

    Pure: the input payload is never mutated. Any problem means *nothing* is
    written -- the returned payload is then the original, so a refusal cannot leave
    a half-edited draft behind.
    """

    if not answers:
        return AnswerOutcome(
            payload=dict(payload),
            problems=(AnswerProblem("answer_invalid", "", "至少要回答一个问题。"),),
        )

    # A merged question answers to any of the codes that asked it, and two answers
    # for the same slot are the same answer given twice.
    index: dict[tuple[str, str], Question] = {}
    for question in questions:
        for code in question.codes or (question.code,):
            index[(code, question.slot)] = question
    new_payload: dict[str, Any] = copy.deepcopy(dict(payload))
    problems: list[AnswerProblem] = []
    applied: list[Applied] = []
    seen: set[str] = set()

    field_values: dict[str, dict[str, Any]] = {}
    indicator_edits: dict[int, dict[str, Any]] = {}
    condition_values: dict[tuple[str, str], dict[str, Any]] = {}
    timeframe: str | None = None

    for answer in answers:
        key = (answer.code, answer.slot)
        if answer.slot in seen:
            problems.append(
                AnswerProblem(
                    "answer_duplicate",
                    answer.slot,
                    f"「{answer.slot}」给了两次答案；一条问题只能答一次。",
                )
            )
            continue
        seen.add(answer.slot)
        question = index.get(key)
        if question is None:
            problems.append(
                AnswerProblem(
                    "answer_not_a_question",
                    answer.slot,
                    f"「{answer.slot}」（{answer.code}）不是这份草案此刻的问题："
                    "编译器现在没有问它，所以不能按它改草案。",
                )
            )
            continue
        if not question.answerable:
            problems.append(AnswerProblem("answer_not_supported", answer.slot, question.reason))
            continue
        if answer.value is None:
            if question.value_optional:
                # "This unknown no longer blocks" is itself an answer.
                applied.append(
                    Applied(
                        code=answer.code,
                        slot=answer.slot,
                        value=None,
                        label=question.label,
                        summary=f"{question.label}：不再当作未知",
                        unknown_fields=_unknown_fields_for(answer.slot),
                    )
                )
                continue
            problems.append(
                AnswerProblem(
                    "answer_invalid", answer.slot, f"「{question.label}」需要一个值，不能留空。"
                )
            )
            continue
        problem, value = _absorb(
            answer,
            question,
            payload=new_payload,
            field_values=field_values,
            indicator_edits=indicator_edits,
            condition_values=condition_values,
        )
        if problem is not None:
            problems.append(problem)
            continue
        if answer.slot == SYSTEM_TIMEFRAME_SLOT:
            timeframe = str(value)
        applied.append(
            Applied(
                code=answer.code,
                slot=answer.slot,
                value=value,
                label=question.label,
                summary=f"{question.label}：{_render(value)}",
                unknown_fields=_unknown_fields_for(answer.slot),
            )
        )

    if problems:
        return AnswerOutcome(payload=dict(payload), problems=tuple(problems))

    # ------------------------------------------------------------ materialise
    rule_for_slot: dict[str, str] = {}
    for field_name in _NUMERIC_FIELDS:
        values = field_values.get(field_name)
        if not values:
            continue
        labels = "、".join(f"{name}={_render(value)}" for name, value in values.items())
        rule_for_slot[field_name] = _append_rule(
            new_payload,
            field_name=field_name,
            parameters=dict(values),
            statement=f"人工回答：{labels}",
        )
    for (field_name, side), parameters in condition_values.items():
        slot = f"{field_name}.{side}"
        rule_for_slot[slot] = _append_rule(
            new_payload,
            field_name=field_name,
            parameters=dict(parameters),
            statement=f"人工回答：{field_name} 的 {side} 条件",
        )
    for position, edits in indicator_edits.items():
        indicators = new_payload.get("indicators")
        if not isinstance(indicators, list) or position >= len(indicators):
            continue
        item = indicators[position]
        if not isinstance(item, dict):
            continue
        parameters = item.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
            item["parameters"] = parameters
        parameters.update(edits)
    if timeframe is not None:
        market = new_payload.get("market")
        if not isinstance(market, dict):
            market = {}
            new_payload["market"] = market
        market["timeframes"] = [timeframe]

    # A rule this run invented is ASSUMED, and the research gate refuses an ASSUMED
    # rule that no assumption names (``validate_draft`` -> ``_disclosure_violations``;
    # the vocabulary is ``rules[].field``, never a rule id -- ADR-154). A person's
    # answer is therefore disclosed exactly the way a model's would be.
    answered_fields: list[str] = [name for name in _NUMERIC_FIELDS if name in field_values]
    answered_fields += [
        field_name for field_name, _side in condition_values if field_name not in answered_fields
    ]
    _disclose_assumptions(new_payload, answered_fields)

    # A slot answered by sub-key (`sizing.fraction`) belongs to the whole field, so
    # the rule id is attached to the parent slot when that is what was answered.
    for answer in answers:
        if answer.slot in rule_for_slot:
            continue
        field_name, _name = _split_slot(answer.slot)
        if field_name and field_name in rule_for_slot:
            rule_for_slot[answer.slot] = rule_for_slot[field_name]

    applied = [
        dataclasses.replace(item, rule_id=rule_for_slot.get(item.slot, item.rule_id))
        for item in applied
    ]

    settled: set[str] = set()
    for answer in answers:
        settled.update(_unknown_fields_for(answer.slot))
    _clear_unknowns(new_payload, settled)
    return AnswerOutcome(payload=new_payload, applied=tuple(applied))


# --------------------------------------------------------------------------- #
# Persistence: one new draft row, one audit event, no write on any failure
# --------------------------------------------------------------------------- #
def draft_hash_of(payload: Mapping[str, Any]) -> str:
    from app.compiler.hashing import draft_hash

    return draft_hash(payload)


def report_for_questions(payload: Mapping[str, Any], *, draft: Any = None) -> dict[str, Any]:
    """Recompute the compile report a draft *currently* gets, for asking only."""

    from app.compiler import compile_strategy_draft
    from app.compiler.models import CompilerInput

    compiler_input = CompilerInput(
        draft=payload,
        strategy_id=QUESTION_TARGET_ID,
        version=QUESTION_TARGET_VERSION,
        draft_id=getattr(draft, "id", None),
        hypothesis_id=getattr(draft, "hypothesis_id", None),
        run_id=getattr(draft, "run_id", None),
        capability_report=getattr(draft, "capability_report_json", None),
    )
    return compile_strategy_draft(
        compiler_input, QUESTION_TARGET_ID, QUESTION_TARGET_VERSION
    ).report


def answer_draft_questions(
    db: Any,
    *,
    draft: Any,
    answers: Sequence[Answer],
    answered_draft_hash: str,
    actor: str = "operator",
) -> tuple[Any, AnswerOutcome, list[Question], str]:
    """Store a person-answered revision of ``draft``.

    Returns ``(new_row, outcome, remaining_questions, previous_draft_hash)``.

    Every refusal is a :class:`Refused` and leaves the database untouched: the
    checks run before the insert, the insert and the audit event share one
    transaction, and the draft handed in is never modified.
    """

    from app.ai import research_schemas as gates
    from app.ai.research import evaluate_draft_payload, store_draft_revision
    from app.data.strategy_service import record_audit
    from app.domain.models import AIResearchRun
    from app.domain.models import StrategyDraft as StrategyDraftRow
    from app.domain.models import StrategyHypothesis as StrategyHypothesisRow

    if not isinstance(draft, StrategyDraftRow):  # pragma: no cover - defensive
        raise Refused(404, "draft_not_found", "strategy draft not found")

    payload = draft.draft_json if isinstance(draft.draft_json, Mapping) else {}
    previous_hash = draft_hash_of(payload)

    if draft.compiled_strategy_version_id is not None:
        raise Refused(
            409,
            "draft_already_compiled",
            f"草案 {draft.id} 已经编译成策略版本 {draft.compiled_strategy_version_id} 了，"
            "编译过的草案不能再改：要改请重新生成一份草案。",
            {"draft_id": draft.id, "strategy_version_id": draft.compiled_strategy_version_id},
        )

    if not answered_draft_hash or answered_draft_hash != previous_hash:
        raise Refused(
            409,
            "draft_hash_mismatch",
            "这份草案在你看过之后已经被改过了（或者你手上的答案对应的不是这一版）："
            f"现在这版的内容哈希是 {previous_hash[:12]}…。"
            "请刷新后重新读一次问题再作答，这次没有任何改动被写入。",
            {"draft_id": draft.id, "draft_hash": previous_hash},
        )

    run = db.get(AIResearchRun, draft.run_id) if draft.run_id else None
    hypothesis_row = (
        db.get(StrategyHypothesisRow, draft.hypothesis_id) if draft.hypothesis_id else None
    )
    if run is None or hypothesis_row is None:
        raise Refused(
            409,
            "draft_detached",
            "这份草案已经和它的研究运行脱开了（找不到对应的运行或假设），"
            "所以没法把答案存成同一批研究里的新一版。",
            {"draft_id": draft.id},
        )

    # A draft keeps its own bytes forever (that is the point of the hash check
    # above), so "the run has already moved on" is invisible to it: a second
    # browser tab still holding the old form would answer the *old* draft and
    # produce a "new version" whose parent is not the previous new version --
    # the version in between would silently drop out of the lineage. Answers may
    # only be stacked on the run's current draft; anything older says so.
    current_draft_id = run.draft_id
    if current_draft_id is not None and current_draft_id != draft.id:
        raise Refused(
            409,
            "draft_superseded",
            f"这份草案已经被同一批研究里更新的一版（草案 {current_draft_id}）取代了："
            "在它上面作答会跳过中间那一版，所以这次回答没有被写入，"
            "服务端一个字节都没有改。请刷新到最新那一版，重新读一次问题再作答。",
            {"draft_id": draft.id, "current_draft_id": current_draft_id},
        )

    report = report_for_questions(payload, draft=draft)
    questions = build_questions(report, payload)
    outcome = apply_answers(payload, answers, questions=questions)
    if outcome.problems:
        raise Refused(
            422,
            "answer_refused",
            "这些答案没有被接受，草案没有改动，也没有生成新草案。",
            {"draft_id": draft.id, "problems": [item.to_dict() for item in outcome.problems]},
        )

    try:
        _draft, decision, _hypothesis = evaluate_draft_payload(
            db, run_id=run.id, hypothesis_row=hypothesis_row, payload=outcome.payload
        )
    except gates.ResearchRejected as rejected:
        raise Refused(
            422,
            "answer_failed_the_gates",
            "这些答案会让草案过不了研究层的门禁，所以没有写入任何东西。"
            "下面每一条都是它给出的原因。",
            {
                "draft_id": draft.id,
                "violations": [violation.as_dict() for violation in rejected.violations],
            },
        ) from rejected

    answered_at = dt.datetime.now(dt.UTC)
    new_row = store_draft_revision(
        db,
        run=run,
        hypothesis_row=hypothesis_row,
        payload=outcome.payload,
        decision=decision,
        model_status=str(outcome.payload.get("status") or "") or None,
    )
    run.draft_id = new_row.id
    run.capability_status = new_row.capability_status

    record_audit(
        db,
        event_type="strategy_draft_answered",
        entity_type="strategy_draft",
        entity_id=str(new_row.id),
        action="draft_answers_applied",
        payload={
            "draft_id": new_row.id,
            "previous_draft_id": draft.id,
            "previous_draft_hash": previous_hash,
            "draft_hash": draft_hash_of(outcome.payload),
            "run_id": run.id,
            "hypothesis_id": hypothesis_row.id,
            "version": new_row.version,
            "answers": [
                {"code": item.code, "slot": item.slot, "value": item.value} for item in answers
            ],
            "applied": [item.to_dict() for item in outcome.applied],
            "draft_status": new_row.status,
            "capability_status": new_row.capability_status,
            "is_human_decision": True,
            "decided_by": "user",
            "answered_by": actor,
            "answered_at": answered_at.isoformat(),
            "strategy_version_created": False,
        },
        actor=actor,
    )
    db.commit()

    remaining = build_questions(
        report_for_questions(outcome.payload, draft=new_row), outcome.payload
    )
    return new_row, outcome, remaining, previous_hash
