"""Research schemas and the four gates a model answer has to pass (ADR-154).

Phase 3 lets the AI *propose* a strategy: research material goes in, a
``StrategyHypothesis`` comes out, and ``STRATEGY_ARCHITECT`` turns that into a
``StrategyDraft``. Nothing in this module executes anything. A draft is data for
a human to read; the compiler that would turn it into a ``StrategySpec`` belongs
to a later version, and a draft explicitly cannot claim to be executable.

The module is deliberately the only place that decides what a model answer may
contain, so the gates live together and can be tested together:

1. **schema** — the answer is parsed into the models below, and an unknown or
   forbidden key is a rejection rather than a silently dropped field. A
   performance figure posing as a result key (``cagr``, ``sharpe``, ...) is an
   *integrity* violation: only the deterministic engine may produce those.
2. **domain** — the required fields carry real content, a rule states something,
   and a capability need names the rule it blocks.
3. **capability** — every indicator, rule requirement and explicit request is
   re-assessed against :mod:`app.capabilities` on the *server*. What the model
   claims about support is recorded, never trusted: a claim stronger than the
   registry is a rejection, a weaker one is kept as evidence.
4. **provenance** — every rule says where it came from (``EXPLICIT`` / ``INFERRED``
   / ``ASSUMED`` / ``UNKNOWN``) and cites evidence that exists in this run, and a
   draft rule may never be *stronger* than the hypothesis rule it derives from.

Free text that states a result (``预计 CAGR 25%``) is not a rejection: it is
marked ``UNVERIFIED`` and recorded as a warning, because deleting what a model
said is worse than labelling it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.capabilities import (
    MODEL_CAPABILITIES,
    UNSUPPORTED_CAPABILITIES,
    CapabilityReport,
    assess,
    supported_tokens,
)
from app.features.engine import normalise_indicator_type

__all__ = [
    "APPLIES_TO_DESCRIPTION",
    "ARCHITECT_ROLE",
    "CAPABILITY_VERDICTS",
    "CONFIDENCES",
    "EVIDENCE_QUOTE_DESCRIPTION",
    "EVIDENCE_REQUIRED_ORIGINS",
    "FORBIDDEN_CONTENT_KEYS",
    "FORBIDDEN_METRIC_KEYS",
    "FORMALIZATION_SCHEMA",
    "FORMALIZE_TASK",
    "MIN_QUOTE_CHARS",
    "ORIGINS",
    "ORIGIN_STRENGTH",
    "QUOTE_REQUIRED_ORIGINS",
    "RESEARCHER_ROLE",
    "RESEARCH_SCHEMA",
    "RESEARCH_TASK",
    "RULE_FIELDS",
    "VERDICT_STRENGTH",
    "Ambiguity",
    "Assumption",
    "CapabilityDecision",
    "CapabilityItem",
    "CapabilityRequest",
    "DraftRule",
    "Evidence",
    "ExperimentalAlternative",
    "IndicatorSpec",
    "MarketSpec",
    "NeedCapability",
    "ResearchRejected",
    "Rule",
    "StrategyDraft",
    "StrategyHypothesis",
    "Unknown",
    "Violation",
    "assess_draft_capabilities",
    "capability_brief",
    "find_forbidden_keys",
    "find_unverified_result_claims",
    "normalise_token",
    "parse_draft",
    "parse_hypothesis",
    "validate_draft",
    "validate_hypothesis",
]

# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #
RESEARCHER_ROLE = "RESEARCHER"
ARCHITECT_ROLE = "STRATEGY_ARCHITECT"
RESEARCH_TASK = "strategy_research"
FORMALIZE_TASK = "strategy_formalization"

#: Where a rule came from. This is a strength order: a draft may weaken a rule it
#: inherited from the hypothesis, never strengthen it.
Origin = Literal["EXPLICIT", "INFERRED", "ASSUMED", "UNKNOWN"]
ORIGINS: tuple[str, ...] = get_args(Origin)
ORIGIN_STRENGTH: dict[str, int] = {"EXPLICIT": 3, "INFERRED": 2, "ASSUMED": 1, "UNKNOWN": 0}

#: An EXPLICIT or INFERRED rule has to cite the source it read; an ASSUMED or
#: UNKNOWN one does not (there is nothing to cite).
EVIDENCE_REQUIRED_ORIGINS: tuple[str, ...] = ("EXPLICIT", "INFERRED")

#: Naming a source is not the same as quoting it. Material the author really
#: wrote is EXPLICIT, so an EXPLICIT rule has to carry the words it rests on —
#: the same requirement as the material's own sentence (ADR-159).
QUOTE_REQUIRED_ORIGINS: tuple[str, ...] = ("EXPLICIT",)

#: A quote shorter than this cannot carry meaning; it is a token, not evidence.
MIN_QUOTE_CHARS = 2

Confidence = Literal["low", "medium", "high"]
CONFIDENCES: tuple[str, ...] = get_args(Confidence)

RuleField = Literal[
    "market",
    "universe",
    "timeframe",
    "indicator",
    "entry",
    "exit",
    "risk",
    "sizing",
    "execution",
    "parameter",
]
RULE_FIELDS: tuple[str, ...] = get_args(RuleField)

#: The one wording of the ``assumptions[].applies_to`` contract. The ``Assumption``
#: model and both model-facing schemas carry it verbatim, so the vocabulary cannot
#: drift between them: ``_disclosure_violations`` compares these values with
#: ``rule.field``, never with a rule id.
APPLIES_TO_DESCRIPTION = (
    "The rule field this assumption fills in, from the same vocabulary as "
    f"rules[].field ({', '.join(RULE_FIELDS)}). Name the field itself, such as "
    "'indicator' — never the rule id, such as 'r-oversold': an ASSUMED rule whose "
    "field no assumption names is refused."
)

#: The one wording of the ``evidence[].quote`` contract. Both model-facing schemas
#: carry it verbatim, so the text the model reads cannot drift away from what
#: ``_quote_span`` enforces: one contiguous passage of the named source, character
#: for character apart from whitespace (ADR-159).
EVIDENCE_QUOTE_DESCRIPTION = (
    "Words copied character for character out of this source — one contiguous passage, "
    "never a summary, a reworded sentence or a re-formatted date or number; only the "
    "amount of whitespace may differ. The server looks the quote up exactly as it is "
    "written and refuses the answer when it is not there: to cite two places, send two "
    "evidence entries, one evidence entry per passage, instead of joining them into one "
    "quote."
)

#: What a draft may be. There is deliberately no "SUPPORTED_AND_EXECUTABLE": a
#: draft is never executable, and the missing capabilities are named as such
#: rather than downgraded away.
CapabilityVerdict = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "NEEDS_CAPABILITY"]
CAPABILITY_VERDICTS: tuple[str, ...] = get_args(CapabilityVerdict)
VERDICT_STRENGTH: dict[str, int] = {
    "NEEDS_CAPABILITY": 0,
    "PARTIALLY_SUPPORTED": 1,
    "SUPPORTED": 2,
}

#: Keys that would be a performance *result*. Only the engine may produce these,
#: so a model that emits one is rejected rather than argued with.
FORBIDDEN_METRIC_KEYS: tuple[str, ...] = (
    "cagr",
    "annual_return",
    "annualised_return",
    "annualized_return",
    "total_return",
    "net_profit",
    "profit",
    "pnl",
    "sharpe",
    "sharpe_ratio",
    "sortino",
    "sortino_ratio",
    "calmar",
    "calmar_ratio",
    "max_drawdown",
    "maximum_drawdown",
    "drawdown",
    "win_rate",
    "profit_factor",
    "expectancy",
    "volatility",
    "exposure",
    "turnover",
    "var",
    "cvar",
    "value_at_risk",
    "equity_curve",
    "backtest_result",
    "backtest_results",
    "backtest_metrics",
)

#: Keys that would make a draft executable, or that reach for the runtime
#: plumbing the model has no business shaping. Exact key match only, so
#: ``order_type`` stays legal while ``order`` does not.
FORBIDDEN_CONTENT_KEYS: tuple[str, ...] = (
    "dsl",
    "strategy_spec",
    "strategy_version_id",
    "compiled",
    "compiled_strategy",
    "compiled_strategy_version_id",
    "python",
    "shell",
    "command",
    "commands",
    "sql",
    "execute",
    "execution_plan",
    "run_backtest",
    "backtest_run_id",
    "broker",
    "order",
    "orders",
    "trade_order",
    "api_key",
    "secret",
    "secrets",
    "system_prompt",
    "role_contract",
)

_STRICT = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Hypothesis models (RESEARCHER)
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    """One citation: which source, and where inside it.

    ``source_ref``, ``locator`` and ``quote`` are the model's; the ``verified``
    block is the server's. The server overwrites it on every validation and the
    schema handed to the model never mentions it, so a stored citation says
    whether the words were really found in the text this run read (ADR-159).
    """

    model_config = _STRICT

    source_ref: str
    locator: str | None = None
    quote: str | None = None
    #: Server-owned verification result; never trusted when it arrives from a model.
    verified: bool = False
    char_start: int | None = None
    char_end: int | None = None
    verified_against: str | None = None


class Rule(BaseModel):
    """One rule of the strategy as the material states or implies it."""

    model_config = _STRICT

    id: str
    field: RuleField
    statement: str
    origin: Origin
    confidence: Confidence = "medium"
    evidence: list[Evidence] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    note: str | None = None


class Assumption(BaseModel):
    """What the model had to fill in, and which rule field it fills it in for.

    ``applies_to`` carries ``rule.field`` values (see :data:`RULE_FIELDS`) — the same
    vocabulary ``rules[].field`` uses — never a rule ``id``: the disclosure gate
    matches an ASSUMED rule by its field (ADR-154).
    """

    model_config = _STRICT

    statement: str
    applies_to: list[str] = Field(default_factory=list, description=APPLIES_TO_DESCRIPTION)
    reason: str | None = None


class Ambiguity(BaseModel):
    """A phrase with more than one reading; only a human may choose."""

    model_config = _STRICT

    phrase: str
    readings: list[str] = Field(default_factory=list)
    needs_decision: bool = True


class Unknown(BaseModel):
    """Something the material does not say and the system needs.

    ``rule_id`` pins the unknown to one hypothesis rule. A field-level entry is
    only an answer when that field carries a single EXPLICIT rule; otherwise one
    vague unknown could excuse several concrete rules (ADR-160).
    """

    model_config = _STRICT

    field: str
    why: str
    needed_to_formalize: bool = True
    rule_id: str | None = None


class CapabilityRequest(BaseModel):
    """A capability the strategy needs, with what the model believes about it."""

    model_config = _STRICT

    capability: str
    statement: str | None = None
    reason: str | None = None
    #: The model's own belief. Recorded as a claim and re-checked on the server;
    #: a belief never changes the registry (ADR-151).
    claimed_supported: bool = False


class StrategyHypothesis(BaseModel):
    """What the researcher understood, rule by rule, before any formalization."""

    model_config = _STRICT

    strategy_name: str
    understanding: str
    rules: list[Rule] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    capability_requests: list[CapabilityRequest] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    objective: str | None = None
    market: list[str] = Field(default_factory=list)
    asset_class: str | None = None
    universe: str | None = None
    timeframe: str | None = None
    limitations: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


# --------------------------------------------------------------------------- #
# Draft models (STRATEGY_ARCHITECT)
# --------------------------------------------------------------------------- #
class MarketSpec(BaseModel):
    model_config = _STRICT

    markets: list[str] = Field(default_factory=list)
    asset_classes: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    universe: str | None = None


class IndicatorSpec(BaseModel):
    model_config = _STRICT

    name: str
    origin: Origin
    parameters: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    note: str | None = None


class DraftRule(BaseModel):
    """A rule of the draft, tied back to the hypothesis rule it came from."""

    model_config = _STRICT

    id: str
    field: RuleField
    statement: str
    origin: Origin
    confidence: Confidence = "medium"
    #: ``Rule.id`` of the hypothesis rule this formalizes. ``None`` means the
    #: architect added a rule of its own, which is only allowed as ASSUMED or
    #: UNKNOWN.
    derived_from: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    note: str | None = None


class NeedCapability(BaseModel):
    """A capability the draft needs and the system does not have."""

    model_config = _STRICT

    capability: str
    affected_rule: str
    reason: str
    suggested_alternative: str | None = None
    alternative_is_experimental: bool = False


class ExperimentalAlternative(BaseModel):
    """A smaller experiment the architect proposes instead of the original."""

    model_config = _STRICT

    label: str
    statement: str
    what_it_gives_up: list[str] = Field(default_factory=list)
    differs_from_original: bool = True


class StrategyDraft(BaseModel):
    """The formalized strategy: richer than the DSL, and deliberately inert."""

    model_config = _STRICT

    strategy_name: str
    status: CapabilityVerdict
    market: MarketSpec
    rules: list[DraftRule] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    required_capabilities: list[NeedCapability] = Field(default_factory=list)
    experimental_alternatives: list[ExperimentalAlternative] = Field(default_factory=list)
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    understanding_of_original: str | None = None
    #: Always false. Present so a model that tries to hand back something
    #: executable is caught by the schema instead of by a reader.
    executable: bool = False


# --------------------------------------------------------------------------- #
# JSON schemas handed to the provider and stored with the prompt
# --------------------------------------------------------------------------- #
RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "strategy_name",
        "understanding",
        "rules",
        "ambiguities",
        "unknowns",
        "capability_requests",
    ],
    "properties": {
        "strategy_name": {"type": "string"},
        "understanding": {"type": "string"},
        "objective": {"type": "string"},
        "market": {"type": "array", "items": {"type": "string"}},
        "asset_class": {"type": "string"},
        "universe": {"type": "string"},
        "timeframe": {"type": "string"},
        "confidence": {"enum": list(CONFIDENCES)},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "field", "statement", "origin"],
                "properties": {
                    "id": {"type": "string"},
                    "field": {"enum": list(RULE_FIELDS)},
                    "statement": {"type": "string"},
                    "origin": {"enum": list(ORIGINS)},
                    "confidence": {"enum": list(CONFIDENCES)},
                    "note": {"type": "string"},
                    "parameters": {"type": "object"},
                    "required_capabilities": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["source_ref"],
                            "properties": {
                                "source_ref": {"type": "string"},
                                "locator": {"type": "string"},
                                "quote": {
                                    "type": "string",
                                    "description": EVIDENCE_QUOTE_DESCRIPTION,
                                },
                            },
                        },
                    },
                },
            },
        },
        "ambiguities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["phrase"],
                "properties": {
                    "phrase": {"type": "string"},
                    "readings": {"type": "array", "items": {"type": "string"}},
                    "needs_decision": {"type": "boolean"},
                },
            },
        },
        "unknowns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "why"],
                "properties": {
                    "field": {"type": "string"},
                    "why": {"type": "string"},
                    "needed_to_formalize": {"type": "boolean"},
                    "rule_id": {"type": "string"},
                },
            },
        },
        "capability_requests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["capability"],
                "properties": {
                    "capability": {"type": "string"},
                    "statement": {"type": "string"},
                    "reason": {"type": "string"},
                    "claimed_supported": {"type": "boolean"},
                },
            },
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement"],
                "properties": {
                    "statement": {"type": "string"},
                    "applies_to": {
                        "type": "array",
                        "items": {"enum": list(RULE_FIELDS)},
                        "description": APPLIES_TO_DESCRIPTION,
                    },
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

FORMALIZATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "strategy_name",
        "status",
        "market",
        "rules",
        "unknowns",
        "required_capabilities",
        "experimental_alternatives",
    ],
    "properties": {
        "strategy_name": {"type": "string"},
        "status": {"enum": list(CAPABILITY_VERDICTS)},
        "executable": {"type": "boolean"},
        "understanding_of_original": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "parameters": {"type": "object"},
        "market": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "markets": {"type": "array", "items": {"type": "string"}},
                "asset_classes": {"type": "array", "items": {"type": "string"}},
                "timeframes": {"type": "array", "items": {"type": "string"}},
                "universe": {"type": "string"},
            },
        },
        "indicators": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "origin"],
                "properties": {
                    "name": {"type": "string"},
                    "origin": {"enum": list(ORIGINS)},
                    "parameters": {"type": "object"},
                    "note": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["source_ref"],
                            "properties": {
                                "source_ref": {"type": "string"},
                                "locator": {"type": "string"},
                                "quote": {
                                    "type": "string",
                                    "description": EVIDENCE_QUOTE_DESCRIPTION,
                                },
                            },
                        },
                    },
                },
            },
        },
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "field", "statement", "origin"],
                "properties": {
                    "id": {"type": "string"},
                    "field": {"enum": list(RULE_FIELDS)},
                    "statement": {"type": "string"},
                    "origin": {"enum": list(ORIGINS)},
                    "confidence": {"enum": list(CONFIDENCES)},
                    "derived_from": {"type": ["string", "null"]},
                    "note": {"type": "string"},
                    "parameters": {"type": "object"},
                    "required_capabilities": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["source_ref"],
                            "properties": {
                                "source_ref": {"type": "string"},
                                "locator": {"type": "string"},
                                "quote": {
                                    "type": "string",
                                    "description": EVIDENCE_QUOTE_DESCRIPTION,
                                },
                            },
                        },
                    },
                },
            },
        },
        "unknowns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "why"],
                "properties": {
                    "field": {"type": "string"},
                    "why": {"type": "string"},
                    "needed_to_formalize": {"type": "boolean"},
                    "rule_id": {"type": "string"},
                },
            },
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement"],
                "properties": {
                    "statement": {"type": "string"},
                    "applies_to": {
                        "type": "array",
                        "items": {"enum": list(RULE_FIELDS)},
                        "description": APPLIES_TO_DESCRIPTION,
                    },
                    "reason": {"type": "string"},
                },
            },
        },
        "required_capabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["capability", "affected_rule", "reason"],
                "properties": {
                    "capability": {"type": "string"},
                    "affected_rule": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggested_alternative": {"type": "string"},
                    "alternative_is_experimental": {"type": "boolean"},
                },
            },
        },
        "experimental_alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "statement"],
                "properties": {
                    "label": {"type": "string"},
                    "statement": {"type": "string"},
                    "what_it_gives_up": {"type": "array", "items": {"type": "string"}},
                    "differs_from_original": {"type": "boolean"},
                },
            },
        },
    },
}

#: Which schema belongs to which task — read by ``role_contracts`` so the
#: ``ai_prompts`` / ``ai_role_contracts`` index rows carry the real schema.
TASK_SCHEMAS: dict[str, dict[str, Any]] = {
    RESEARCH_TASK: RESEARCH_SCHEMA,
    FORMALIZE_TASK: FORMALIZATION_SCHEMA,
}


# --------------------------------------------------------------------------- #
# Rejection vocabulary
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Violation:
    """One reason a model answer was refused."""

    code: str
    message: str
    severity: str = "blocker"
    field_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "field": self.field_name,
        }


class ResearchRejected(Exception):
    """A research step failed a gate. Nothing was stored as a result."""

    def __init__(self, violations: list[Violation], *, step: str = "") -> None:
        self.violations = list(violations)
        self.step = step
        first = self.violations[0].message if self.violations else "rejected"
        super().__init__(f"{step or 'research'} rejected: {first}")

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(violation.code for violation in self.violations)

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "violations": [v.as_dict() for v in self.violations]}


# --------------------------------------------------------------------------- #
# Key scans (schema gate)
# --------------------------------------------------------------------------- #
def normalise_token(token: Any) -> str:
    """Fold a capability token the same way the registry does (ADR-151)."""

    return str(token).strip().lower().replace(" ", "_")


def _classify_key(key: str) -> str | None:
    normalised = normalise_token(key)
    if normalised in FORBIDDEN_METRIC_KEYS:
        return "fabricated_metric"
    if normalised in FORBIDDEN_CONTENT_KEYS:
        return "forbidden_content"
    return None


def find_forbidden_keys(payload: Any, *, path: str = "") -> list[Violation]:
    """Every forbidden key anywhere in the payload, with the path that holds it.

    Keys are checked anywhere, not only at the top level: a performance figure
    hidden inside ``parameters`` is the same claim as one at the top.
    """

    found: list[Violation] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else str(key)
            code = _classify_key(str(key))
            if code is not None:
                if code == "fabricated_metric":
                    message = (
                        f"'{here}' is a performance result. Only the deterministic engine "
                        "may produce a result figure; this version never ran a backtest."
                    )
                else:
                    message = f"'{here}' is not something a research draft may contain."
                found.append(Violation(code=code, message=message, field_name=here))
            found.extend(find_forbidden_keys(value, path=here))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(find_forbidden_keys(value, path=f"{path}[{index}]"))
    return found


_ASCII_CLAIM = re.compile(
    r"\b(cagr|sharpe(?:\s*ratio)?|sortino(?:\s*ratio)?|calmar(?:\s*ratio)?|"
    r"max(?:imum)?\s*drawdown|drawdown|win\s*rate|profit\s*factor|expectancy|"
    r"annuali[sz]ed\s*return|total\s*return|volatility|value\s*at\s*risk|var|cvar)\b",
    re.IGNORECASE,
)
_CJK_CLAIM = re.compile(
    r"(年化收益|年化回报|年化收益率|总收益|收益率|最大回撤|回撤|夏普|索提诺|卡玛|胜率|盈亏比|期望收益|波动率|风险价值)"
)
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")
_DIGIT = re.compile(r"\d")


def find_unverified_result_claims(payload: Any) -> list[dict[str, Any]]:
    """Result figures a model stated in free text, marked ``UNVERIFIED``.

    Not a rejection: the claim may be the source's own words. It is recorded so
    a reader sees "unverified claim" instead of a number that looks computed.
    """

    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            for sentence in _SENTENCE_SPLIT.split(value):
                text = sentence.strip()
                if not text or not _DIGIT.search(text):
                    continue
                match = _ASCII_CLAIM.search(text) or _CJK_CLAIM.search(text)
                if match is None:
                    continue
                metric = match.group(0).strip().lower()
                key = (metric, text[:200])
                if key in seen:
                    continue
                seen.add(key)
                claims.append(
                    {
                        "kind": "UNVERIFIED",
                        "metric": metric,
                        "text": text[:200],
                        "note": (
                            "stated by the model or by material it read; this system did not "
                            "compute it and cannot confirm it"
                        ),
                    }
                )

    visit(payload)
    return claims


# --------------------------------------------------------------------------- #
# Schema gate: parse an answer into the models
# --------------------------------------------------------------------------- #
def _schema_violations(exc: ValidationError) -> list[Violation]:
    violations: list[Violation] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        kind = str(error.get("type", ""))
        if kind == "extra_forbidden" or kind == "missing":
            key = str(error["loc"][-1])
            code = _classify_key(key) or "schema_invalid"
            if kind == "missing":
                message = f"missing required field '{key}'"
            elif code == "fabricated_metric":
                message = (
                    f"unexpected field '{location}': a performance result is not something "
                    "this system accepts from a model"
                )
            elif code == "forbidden_content":
                message = (
                    f"unexpected field '{location}': not something a research answer may contain"
                )
            else:
                message = f"unexpected field '{location}'"
            violations.append(Violation(code=code, message=message, field_name=location))
        else:
            violations.append(
                Violation(
                    code="schema_invalid",
                    message=f"{location}: {error.get('msg', 'invalid value')}",
                    field_name=location,
                )
            )
    return violations


def _parse(model: type[BaseModel], data: Any, *, step: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ResearchRejected(_schema_violations(exc), step=step) from exc


def parse_hypothesis(data: Any) -> StrategyHypothesis:
    """Parse a RESEARCHER answer, or raise ``ResearchRejected``."""

    return _parse(StrategyHypothesis, data, step=RESEARCH_TASK)


def parse_draft(data: Any) -> StrategyDraft:
    """Parse a STRATEGY_ARCHITECT answer, or raise ``ResearchRejected``."""

    return _parse(StrategyDraft, data, step=FORMALIZE_TASK)


# --------------------------------------------------------------------------- #
# Provenance helpers
# --------------------------------------------------------------------------- #
def _evidence_refs(evidence: list[Evidence]) -> list[str]:
    return [item.source_ref.strip() for item in evidence if item.source_ref.strip()]


_WHITESPACE = re.compile(r"\s+")


def _normalise_whitespace(text: str) -> str:
    """Collapse every whitespace run to a single space (ADR-159)."""

    return _WHITESPACE.sub(" ", text).strip()


def _digest_text(text: str) -> str:
    """Hash of the text a citation was checked against.

    Kept identical to ``app.ai.research._digest``: a run records the same value
    as the artifact's ``text_hash``, which is what makes "the AI read this
    version" a checkable statement rather than a note.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """Where ``quote`` sits inside ``text``, or ``None`` when it is not there.

    Wrapping is not content: a model may re-flow a sentence across lines, so
    whitespace runs are allowed to differ while every other character has to
    match exactly, in order. The span points into the original text so a human
    can open the material at it.
    """

    wanted = _normalise_whitespace(quote)
    if not wanted:
        return None
    pattern = r"\s+".join(re.escape(token) for token in wanted.split(" "))
    match = re.search(pattern, text)
    if match is None:
        return None
    return match.start(), match.end()


def _clear_verification(evidence: Evidence) -> None:
    """A citation that did not verify must not be able to look verified."""

    evidence.verified = False
    evidence.char_start = None
    evidence.char_end = None
    evidence.verified_against = None


#: A run of words this short is not treated as "material the source really contains"
#: when explaining a refusal. Diagnostic only, and deliberately conservative.
_EDGE_MIN_CHARS = 8


def _real_edge(text: str, tokens: list[str], *, from_end: bool) -> str:
    """The longest run of the quote's words at one end that really is in the source."""

    for size in range(len(tokens) - 1, 0, -1):
        part = tokens[-size:] if from_end else tokens[:size]
        candidate = " ".join(part)
        if len(candidate) >= _EDGE_MIN_CHARS and _quote_span(text, candidate) is not None:
            return candidate
    return ""


def _mismatch_kind(text: str, quote: str) -> str:
    """Explain *which way* a refused quote fails: ``stitched``, ``partial``, ``absent``.

    Diagnostic only: the quote is already refused and nothing here can accept one. A
    quote the model joined out of separate passages has a real run of words at each end
    that do not meet in the middle; a quote mixing the source's words with words of its
    own has such a run at one end only; a quote that is not in the source at all —
    a summary, a reworded sentence, a re-formatted value — has neither.
    """

    wanted = _normalise_whitespace(quote)
    tokens = wanted.split(" ")
    if len(tokens) < 2:
        return "absent"
    head = _real_edge(text, tokens, from_end=False)
    tail = _real_edge(text, tokens, from_end=True)
    if head and tail and len(head) + len(tail) < len(wanted):
        return "stitched"
    if head or tail:
        return "partial"
    return "absent"


def _mismatch_message(*, where: str, ref: str, quote: str, text: str) -> str:
    """Say why the words were not found, without widening what counts as a match.

    Every branch is the same ``evidence_mismatch`` and the same strict lookup; only the
    wording differs, because the fix differs: a stitched quote needs one evidence entry
    per passage, a partly-real quote needs the words the source does not contain dropped,
    and a quote that is not there at all needs to become a real contiguous passage.
    """

    kind = _mismatch_kind(text, quote)
    if kind == "stitched":
        return (
            f"{where} quotes '{quote}', which is not one contiguous passage of '{ref}': it reads "
            "as two real passages of the source joined together rather than one. Send one "
            "evidence entry per passage instead of joining them into a single quote"
        )
    if kind == "partial":
        return (
            f"{where} quotes '{quote}', of which only part appears in '{ref}'; a quote is copied "
            "character for character out of the source, one contiguous passage at a time, so the "
            "words around it that the source does not contain cannot be part of it"
        )
    return (
        f"{where} quotes '{quote}', which does not appear in '{ref}' as one contiguous passage; "
        "a quote is copied character for character out of the source — a summary, a reworded "
        "sentence and a re-formatted date or number are refused, and two passages are two "
        "evidence entries"
    )


def _check_evidence(
    *,
    where: str,
    origin: str,
    evidence: list[Evidence],
    sources: dict[str, str],
    source_hashes: dict[str, str] | None = None,
) -> list[Violation]:
    """Check a citation: does the source exist, and are the words really in it?

    Every citation is checked whatever its origin, because a made-up quote is a
    fabrication whether the rule is EXPLICIT or ASSUMED. What the origin decides
    is whether a quote is *required*: material the author really wrote can be
    quoted, so an EXPLICIT rule has to quote it (ADR-159).
    """

    violations: list[Violation] = []
    refs = _evidence_refs(evidence)
    if origin in EVIDENCE_REQUIRED_ORIGINS and not refs:
        violations.append(
            Violation(
                code="evidence_missing",
                message=(
                    f"{where} is labelled {origin} but cites nothing; only ASSUMED or UNKNOWN "
                    "rules may go uncited"
                ),
                field_name=where,
            )
        )
    reported_sources: set[str] = set()
    for item in evidence:
        _clear_verification(item)
        ref = item.source_ref.strip()
        if not ref:
            continue
        text = sources.get(ref)
        if text is None:
            if ref not in reported_sources:
                reported_sources.add(ref)
                violations.append(
                    Violation(
                        code="evidence_unknown_source",
                        message=(
                            f"{where} cites '{ref}', which is not part of this run; evidence must "
                            "point at material the system actually supplied"
                        ),
                        field_name=where,
                    )
                )
            continue
        quote = _normalise_whitespace(item.quote or "")
        if not quote:
            if origin in QUOTE_REQUIRED_ORIGINS:
                violations.append(
                    Violation(
                        code="evidence_missing_quote",
                        message=(
                            f"{where} is labelled {origin} and names '{ref}' without quoting it; "
                            "what the author actually wrote can be quoted, so quote it"
                        ),
                        field_name=where,
                    )
                )
            continue
        if len(quote) < MIN_QUOTE_CHARS:
            violations.append(
                Violation(
                    code="evidence_quote_too_short",
                    message=(
                        f"{where} quotes '{quote}', which is shorter than {MIN_QUOTE_CHARS} "
                        "characters; that is a token, not evidence"
                    ),
                    field_name=where,
                )
            )
            continue
        span = _quote_span(text, quote)
        if span is None:
            violations.append(
                Violation(
                    code="evidence_mismatch",
                    message=_mismatch_message(where=where, ref=ref, quote=quote, text=text),
                    field_name=where,
                )
            )
            continue
        item.verified = True
        item.char_start, item.char_end = span
        item.verified_against = (source_hashes or {}).get(ref) or _digest_text(text)
    return violations


def _unknowns_cover(
    *, rule_id: str, field: str, unknowns: list[Unknown], unknown_fields: set[str]
) -> bool:
    """Is an open rule answered — by name, or by an unambiguous field entry?"""

    if any((unknown.rule_id or "").strip() == rule_id for unknown in unknowns):
        return True
    return field.strip().lower() in unknown_fields


def _disclosure_violations(
    *,
    rules: list[Any],
    assumptions: list[Assumption],
    unknowns: list[Unknown],
) -> list[Violation]:
    violations: list[Violation] = []
    disclosed = {field for assumption in assumptions for field in assumption.applies_to}
    unknown_fields = {unknown.field.strip().lower() for unknown in unknowns}
    for rule in rules:
        if rule.origin == "ASSUMED" and rule.field not in disclosed:
            violations.append(
                Violation(
                    code="assumed_not_disclosed",
                    message=(
                        f"rule '{rule.id}' ({rule.field}) is ASSUMED but no assumption "
                        f"names its field; assumptions[].applies_to must contain the rule "
                        f"field '{rule.field}', never the rule id '{rule.id}'"
                    ),
                    field_name=rule.id,
                )
            )
        if rule.origin == "UNKNOWN" and not _unknowns_cover(
            rule_id=rule.id,
            field=rule.field,
            unknowns=unknowns,
            unknown_fields=unknown_fields,
        ):
            violations.append(
                Violation(
                    code="unknown_not_disclosed",
                    message=(
                        f"rule '{rule.id}' ({rule.field}) is UNKNOWN but the unknowns list does "
                        "not mention that field or that rule id"
                    ),
                    field_name=rule.id,
                )
            )
    return violations


def _unknown_rule_id_violations(
    *, unknowns: list[Unknown], known_rule_ids: set[str]
) -> list[Violation]:
    """An unknown may pin itself to a rule; that rule has to exist."""

    violations: list[Violation] = []
    for unknown in unknowns:
        named = (unknown.rule_id or "").strip()
        if named and named not in known_rule_ids:
            violations.append(
                Violation(
                    code="unknown_rule_unknown",
                    message=(
                        f"unknown for field '{unknown.field}' names rule '{named}', which is not "
                        "a rule of this hypothesis"
                    ),
                    field_name=named,
                )
            )
    return violations


def _duplicate_rule_ids(rules: list[Any]) -> list[Violation]:
    seen: dict[str, int] = {}
    violations: list[Violation] = []
    for rule in rules:
        seen[rule.id] = seen.get(rule.id, 0) + 1
    for rule_id, count in seen.items():
        if count > 1:
            violations.append(
                Violation(
                    code="duplicate_rule_id",
                    message=f"rule id '{rule_id}' is used {count} times; ids must be unique",
                    field_name=rule_id,
                )
            )
    return violations


# --------------------------------------------------------------------------- #
# Domain gate
# --------------------------------------------------------------------------- #
def validate_hypothesis(
    hypothesis: StrategyHypothesis,
    *,
    sources: dict[str, str],
    source_hashes: dict[str, str] | None = None,
) -> list[Violation]:
    """Everything the hypothesis must satisfy beyond its schema."""

    violations: list[Violation] = []
    if not hypothesis.rules and not hypothesis.unknowns:
        violations.append(
            Violation(
                code="domain_invalid",
                message=(
                    "a hypothesis must state at least one rule or name what is missing; "
                    "silence is not a research result"
                ),
            )
        )
    violations.extend(_duplicate_rule_ids(hypothesis.rules))
    for rule in hypothesis.rules:
        if not rule.statement.strip():
            violations.append(
                Violation(
                    code="domain_invalid",
                    message=f"rule '{rule.id}' has an empty statement",
                    field_name=rule.id,
                )
            )
        violations.extend(
            _check_evidence(
                where=f"rule '{rule.id}'",
                origin=rule.origin,
                evidence=rule.evidence,
                sources=sources,
                source_hashes=source_hashes,
            )
        )
    violations.extend(
        _disclosure_violations(
            rules=hypothesis.rules,
            assumptions=hypothesis.assumptions,
            unknowns=hypothesis.unknowns,
        )
    )
    violations.extend(
        _unknown_rule_id_violations(
            unknowns=hypothesis.unknowns,
            known_rule_ids={rule.id for rule in hypothesis.rules},
        )
    )
    for ambiguity in hypothesis.ambiguities:
        if not ambiguity.readings:
            violations.append(
                Violation(
                    code="domain_invalid",
                    message=(
                        f"ambiguity '{ambiguity.phrase}' lists no readings; an ambiguity has to "
                        "say what it could mean"
                    ),
                    field_name=ambiguity.phrase,
                )
            )
    return violations


def validate_draft(
    draft: StrategyDraft,
    *,
    hypothesis: StrategyHypothesis,
    sources: dict[str, str],
    source_hashes: dict[str, str] | None = None,
) -> list[Violation]:
    """Everything the draft must satisfy beyond its schema."""

    violations: list[Violation] = []
    if draft.executable:
        violations.append(
            Violation(
                code="not_executable",
                message=(
                    "a strategy draft is never executable: it has to be compiled and validated "
                    "before anything may run it"
                ),
                field_name="executable",
            )
        )
    violations.extend(_duplicate_rule_ids(draft.rules))
    by_id = {rule.id: rule for rule in hypothesis.rules}

    for rule in draft.rules:
        if not rule.statement.strip():
            violations.append(
                Violation(
                    code="domain_invalid",
                    message=f"rule '{rule.id}' has an empty statement",
                    field_name=rule.id,
                )
            )
        violations.extend(
            _check_evidence(
                where=f"rule '{rule.id}'",
                origin=rule.origin,
                evidence=rule.evidence,
                sources=sources,
                source_hashes=source_hashes,
            )
        )
        if rule.derived_from is None:
            if rule.origin not in ("ASSUMED", "UNKNOWN"):
                violations.append(
                    Violation(
                        code="new_rule_must_be_assumed",
                        message=(
                            f"rule '{rule.id}' derives from nothing but is labelled {rule.origin}; "
                            "a rule the material never stated can only be ASSUMED or UNKNOWN"
                        ),
                        field_name=rule.id,
                    )
                )
            continue
        source_rule = by_id.get(rule.derived_from)
        if source_rule is None:
            violations.append(
                Violation(
                    code="unknown_derivation",
                    message=(
                        f"rule '{rule.id}' derives from '{rule.derived_from}', which is not a rule "
                        "of this hypothesis"
                    ),
                    field_name=rule.id,
                )
            )
            continue
        if source_rule.field != rule.field:
            violations.append(
                Violation(
                    code="derivation_field_mismatch",
                    message=(
                        f"rule '{rule.id}' ({rule.field}) derives from a {source_rule.field} rule"
                    ),
                    field_name=rule.id,
                )
            )
        if ORIGIN_STRENGTH[rule.origin] > ORIGIN_STRENGTH[source_rule.origin]:
            violations.append(
                Violation(
                    code="provenance_stronger_than_hypothesis",
                    message=(
                        f"rule '{rule.id}' is {rule.origin} while the rule it derives from is "
                        f"{source_rule.origin}: a draft may weaken provenance, never strengthen it"
                    ),
                    field_name=rule.id,
                )
            )

    # An EXPLICIT rule of the hypothesis is the author's own intent. Dropping it
    # silently changes the strategy, so it has to be formalized or named missing.
    # A field-level unknown only answers for a field that carries a single
    # EXPLICIT rule: otherwise one vague entry would excuse several concrete
    # rules, and the draft would read as complete when it is not (ADR-160).
    unknown_fields = {unknown.field.strip().lower() for unknown in draft.unknowns}
    derived = {rule.derived_from for rule in draft.rules}
    explicit_per_field: dict[str, int] = {}
    for rule in hypothesis.rules:
        if rule.origin == "EXPLICIT":
            key = rule.field.strip().lower()
            explicit_per_field[key] = explicit_per_field.get(key, 0) + 1
    for rule in hypothesis.rules:
        if rule.origin != "EXPLICIT":
            continue
        if rule.id in derived:
            continue
        field = rule.field.strip().lower()
        if any((unknown.rule_id or "").strip() == rule.id for unknown in draft.unknowns):
            continue
        if explicit_per_field.get(field, 0) == 1 and field in unknown_fields:
            continue
        violations.append(
            Violation(
                code="dropped_explicit_rule",
                message=(
                    f"hypothesis rule '{rule.id}' ({rule.field}) is EXPLICIT but the draft neither "
                    "formalizes it, names it in unknowns[].rule_id, nor covers it with a "
                    "field-level unknown (which only answers when the field carries one EXPLICIT)"
                ),
                field_name=rule.id,
            )
        )

    # The same reasoning for what the research could not pin down: a draft that
    # forgets an open question reads as more complete than the research was.
    for rule in hypothesis.rules:
        if rule.origin != "UNKNOWN":
            continue
        if _unknowns_cover(
            rule_id=rule.id,
            field=rule.field,
            unknowns=draft.unknowns,
            unknown_fields=unknown_fields,
        ):
            continue
        violations.append(
            Violation(
                code="dropped_unknown",
                message=(
                    f"hypothesis rule '{rule.id}' ({rule.field}) is UNKNOWN but the draft's "
                    "unknowns do not mention that field or that rule id"
                ),
                field_name=rule.id,
            )
        )

    violations.extend(
        _unknown_rule_id_violations(unknowns=draft.unknowns, known_rule_ids=set(by_id))
    )

    for indicator in draft.indicators:
        violations.extend(
            _check_evidence(
                where=f"indicator '{indicator.name}'",
                origin=indicator.origin,
                evidence=indicator.evidence,
                sources=sources,
                source_hashes=source_hashes,
            )
        )

    rule_ids = {rule.id for rule in draft.rules}
    for need in draft.required_capabilities:
        if need.affected_rule not in rule_ids:
            violations.append(
                Violation(
                    code="unknown_affected_rule",
                    message=(
                        f"capability '{need.capability}' names rule '{need.affected_rule}', which "
                        "is not a rule of this draft"
                    ),
                    field_name=need.capability,
                )
            )
        if need.suggested_alternative and not need.alternative_is_experimental:
            violations.append(
                Violation(
                    code="alternative_not_marked_experimental",
                    message=(
                        f"the alternative for '{need.capability}' is not marked experimental; a "
                        "reduced experiment is not the original strategy"
                    ),
                    field_name=need.capability,
                )
            )

    violations.extend(
        _disclosure_violations(
            rules=draft.rules, assumptions=draft.assumptions, unknowns=draft.unknowns
        )
    )

    decision = assess_draft_capabilities(draft, hypothesis)
    if VERDICT_STRENGTH[draft.status] > VERDICT_STRENGTH[decision.verdict]:
        violations.append(
            Violation(
                code="capability_overclaim",
                message=(
                    f"the draft claims {draft.status} while the capability registry supports "
                    f"{decision.verdict} (missing: {', '.join(decision.missing) or 'none'}, "
                    f"partial: {', '.join(decision.partial) or 'none'})"
                ),
                field_name="status",
            )
        )
    return violations


# --------------------------------------------------------------------------- #
# Capability gate (server side, never the model's word)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CapabilityItem:
    """One requested capability and what the registry says about it."""

    capability: str
    status: str
    reason: str = ""
    required_by: str = ""
    affected_rule: str | None = None
    claimed_supported: bool = False
    suggested_alternative: str | None = None
    alternative_is_experimental: bool = False

    @property
    def overclaimed(self) -> bool:
        return self.claimed_supported and self.status != "SUPPORTED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "reason": self.reason,
            "required_by": self.required_by,
            "affected_rule": self.affected_rule,
            "claimed_supported": self.claimed_supported,
            "overclaimed": self.overclaimed,
            "suggested_alternative": self.suggested_alternative,
            "alternative_is_experimental": self.alternative_is_experimental,
        }


@dataclass(frozen=True)
class CapabilityDecision:
    """The capability verdict for one draft, computed on this server."""

    verdict: str
    requested: tuple[str, ...] = ()
    supported: tuple[str, ...] = ()
    partial: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    model_capabilities: tuple[str, ...] = ()
    items: tuple[CapabilityItem, ...] = ()
    reasons: dict[str, str] = dataclass_field(default_factory=dict)

    @property
    def overclaims(self) -> tuple[CapabilityItem, ...]:
        return tuple(item for item in self.items if item.overclaimed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "requested": list(self.requested),
            "supported": list(self.supported),
            "partial": list(self.partial),
            "missing": list(self.missing),
            "model_capabilities": list(self.model_capabilities),
            "reasons": dict(self.reasons),
            "items": [item.as_dict() for item in self.items],
        }


def _requested_tokens(
    draft: StrategyDraft, hypothesis: StrategyHypothesis
) -> list[tuple[str, str, str | None]]:
    """``(token, required_by, affected_rule)`` in a stable order, deduplicated."""

    ordered: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()

    def add(token: str, required_by: str, affected_rule: str | None = None) -> None:
        cleaned = str(token).strip()
        if not cleaned:
            return
        key = normalise_token(cleaned)
        if key in seen:
            return
        seen.add(key)
        ordered.append((cleaned, required_by, affected_rule))

    for request in hypothesis.capability_requests:
        add(request.capability, "hypothesis", None)
    for rule in hypothesis.rules:
        for token in rule.required_capabilities:
            add(token, "hypothesis_rule", rule.id)
    for indicator in draft.indicators:
        add(normalise_indicator_type(indicator.name), "indicator", None)
    for rule in draft.rules:
        for token in rule.required_capabilities:
            add(token, "draft_rule", rule.id)
    for need in draft.required_capabilities:
        add(need.capability, "draft", need.affected_rule)
    return ordered


def assess_draft_capabilities(
    draft: StrategyDraft, hypothesis: StrategyHypothesis
) -> CapabilityDecision:
    """Re-check every capability a draft needs against the registry (ADR-151).

    The verdict is the *server's*: a capability the registry does not have is named
    rather than dropped, and a partial capability (short selling) is reported as
    partial. A draft that needs something missing *and* something that exists is
    PARTIALLY_SUPPORTED — never SUPPORTED, because the draft would have to give a
    rule up to be built, and never NEEDS_CAPABILITY, because part of it is real.
    """

    claimed = {
        normalise_token(request.capability): request.claimed_supported
        for request in hypothesis.capability_requests
    }
    alternatives = {normalise_token(need.capability): need for need in draft.required_capabilities}

    requested = _requested_tokens(draft, hypothesis)
    system_tokens = [
        token for token, _, _ in requested if normalise_token(token) not in MODEL_CAPABILITIES
    ]
    model_tokens = [
        token for token, _, _ in requested if normalise_token(token) in MODEL_CAPABILITIES
    ]

    report: CapabilityReport = assess(system_tokens)
    supported = set(report.supported)
    partial = set(report.partial)

    items: list[CapabilityItem] = []
    for token, required_by, affected_rule in requested:
        key = normalise_token(token)
        if key in MODEL_CAPABILITIES:
            continue
        if key in supported:
            status = "SUPPORTED"
        elif key in partial:
            status = "PARTIALLY_SUPPORTED"
        else:
            status = "UNSUPPORTED"
        need = alternatives.get(key)
        items.append(
            CapabilityItem(
                capability=token,
                status=status,
                reason=report.reasons.get(key, ""),
                required_by=required_by,
                affected_rule=affected_rule or (need.affected_rule if need else None),
                claimed_supported=bool(claimed.get(key, False)),
                suggested_alternative=need.suggested_alternative if need else None,
                alternative_is_experimental=bool(need.alternative_is_experimental)
                if need
                else False,
            )
        )

    if report.missing and not (report.supported or report.partial):
        # Nothing to build with: every capability the draft asked for is absent.
        verdict = "NEEDS_CAPABILITY"
    elif report.missing or report.partial:
        # Part of the request is real and part of it is not. The draft can only be
        # built by giving something up, so it may never read as SUPPORTED (the §8
        # silent-downgrade case), and the missing part stays named in the report.
        verdict = "PARTIALLY_SUPPORTED"
    else:
        verdict = "SUPPORTED"

    return CapabilityDecision(
        verdict=verdict,
        requested=tuple(token for token, _, _ in requested),
        supported=tuple(sorted(supported)),
        partial=tuple(sorted(partial)),
        missing=tuple(sorted(report.missing)),
        model_capabilities=tuple(model_tokens),
        items=tuple(items),
        reasons=dict(report.reasons),
    )


def capability_brief() -> dict[str, Any]:
    """The registry, small enough to put in a prompt.

    A role is told what exists before it proposes anything, so "the system does
    not have this" is a conclusion it can reach itself instead of a surprise at
    validation time.
    """

    return {
        "supported_tokens": sorted(supported_tokens()),
        "unsupported": [
            {
                "token": capability.token,
                "label": capability.label,
                "reason": capability.reason,
                "partial": capability.partial,
            }
            for capability in UNSUPPORTED_CAPABILITIES
        ],
        "model_capabilities": list(MODEL_CAPABILITIES),
        "rules": [
            "Propose only tokens from supported_tokens or an explicit unsupported entry.",
            "Never substitute a capability the registry does not have; name it as missing.",
            "A reduced experiment is not the original strategy and must be marked experimental.",
        ],
    }
