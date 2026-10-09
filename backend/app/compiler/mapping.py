"""Canonical parameters, indicator declarations and the frozen key vocabularies.

docs/29 §10 defines one legal parameter shape for a draft rule, §10.5/§10.6
define how an indicator declaration becomes an id, and §12 lists the values a
compiler may never assume. Everything in this module is about reading what the
draft actually said — never about guessing what it probably meant.

`statement`, `note`, `why`, `reason`, `phrase` and `quote` are prose: they are
never read here (docs/29 §6.2), so a rewording cannot move a single value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, get_args

from app.compiler.hashing import canonical_json
from app.features.catalogue import FEATURE_CATALOGUE
from app.features.engine import SUPPORTED_INDICATOR_TYPES, normalise_indicator_type
from app.strategies.dsl import ComparisonOp, FillModel, OrderType, SizingMode
from app.strategies.validator import BASE_COLUMNS, KNOWN_DERIVED

OPERATORS: tuple[str, ...] = get_args(ComparisonOp)
FILL_MODELS: tuple[str, ...] = get_args(FillModel)
ORDER_TYPES: tuple[str, ...] = get_args(OrderType)
SIZING_MODES: tuple[str, ...] = get_args(SizingMode)

# docs/29 §13.2: the validator's column vocabulary is wider than the columns the engine
# actually materialises, so a name can pass `validate_strategy` and still be a silent
# no-op at run time. `FEATURE_CATALOGUE` is the engine's own listing — it is compared
# against `build_features` output in both directions by `tests/test_feature_catalogue.py`
# — so the compiler *reads* it instead of keeping a second list that could drift away
# from the code. Only the names the catalogue does not carry are engine-unreachable;
# a declared indicator's own id is materialised from `spec.indicators` and never
# reaches this set.
ENGINE_FEATURE_COLUMNS: frozenset[str] = frozenset(spec.name for spec in FEATURE_CATALOGUE)

#: Derived column names the validator accepts but the engine cannot produce. Computed,
#: never hand-copied: it shrinks to the empty set the moment the engine grows a column.
#: (`highest_high_20`, `lowest_low_20`, `previous_high`, `previous_low`,
#: `rolling_high_prev`, `rolling_low_prev`, `rsi` at v2.1.0.)
DRIFTED_DERIVED_COLUMNS: frozenset[str] = frozenset(
    KNOWN_DERIVED - ENGINE_FEATURE_COLUMNS - set(BASE_COLUMNS)
)

# §10.3: the only keys a canonical condition may carry, plus the ones it may not
# carry at all. The forbidden list exists so the legacy `{indicator, threshold}`
# shape is refused instead of helpfully repaired (§10.2).
CONDITION_KEYS: tuple[str, ...] = ("left", "operator", "right", "period", "side", "combine")
FORBIDDEN_PARAMETER_KEYS: tuple[str, ...] = (
    "indicator",
    "threshold",
    "condition",
    "conditions",
    "expr",
    "expression",
    "dsl",
    "strategy_spec",
    "id",
    "type",
    "input",
)

# §10.6: a declaration names an indicator; it produces no condition.
INDICATOR_DECLARATION_KEYS: tuple[str, ...] = ("name", "period", "input")

# §11.1: flat risk keys only. A nested block or a unitless key is refused.
RISK_KEYS: tuple[str, ...] = (
    "stop_loss_atr_multiple",
    "take_profit_r_multiple",
    "take_profit_atr_multiple",
    "max_position_pct",
)
SIZING_KEYS: tuple[str, ...] = ("mode", "fraction", "risk_pct", "atr_multiple")
EXECUTION_KEYS: tuple[str, ...] = (
    "fill_model",
    "entry_order_type",
    "limit_offset_atr",
    "stop_offset_atr",
    "order_valid_bars",
    "fee_bps",
    "slippage_bps",
    "initial_capital",
    "allow_fractional",
)
# Product decision A1: `allow_fractional` keeps the DSL's mechanical default. It
# is not written into the specification, it is not a user-required slot and it
# produces no rejection.
IGNORED_EXECUTION_KEYS: tuple[str, ...] = ("allow_fractional",)

MARKET_RULE_KEYS: tuple[str, ...] = ("markets", "universe", "asset_classes", "timeframes")
TIMEFRAME_RULE_KEYS: tuple[str, ...] = ("timeframe",)

# §9.1 row 2: an unknown blocks the draft only in these five fields. Others are
# recorded by the research layer and are not turned into a compiler rejection.
BLOCKING_UNKNOWN_FIELDS: tuple[str, ...] = ("timeframe", "entry", "exit", "risk", "sizing")

SYSTEM_UNIVERSE_SLOT = "market.universe"
SYSTEM_MARKETS_SLOT = "market.markets"
SYSTEM_TIMEFRAME_SLOT = "market.timeframes"


@dataclass(frozen=True)
class ParameterIssue:
    """A refusal produced while reading parameters, with its contract code."""

    code: str
    detail: str


@dataclass(frozen=True)
class CanonicalCondition:
    """One canonical condition (§10.3), before indicator ids are resolved."""

    left: str
    operator: str
    right: str
    period: int | None = None
    side: str | None = None
    combine: str | None = None
    right_is_operand: bool = False

    def to_dsl(self, left: str | None = None, right: str | None = None) -> dict[str, str]:
        return {
            "op": self.operator,
            "left": self.left if left is None else left,
            "right": self.right if right is None else right,
        }


def is_number(value: Any) -> bool:
    """A JSON number, never a bool (`true` is not a threshold)."""

    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number_literal(value: Any) -> str:
    """§10.4: one deterministic way for a number to become spec text.

    `g` is the conversion docs/29 §10.4 freezes for a numeric operand, spelled with
    the builtin `format` so the lint profile's UP031 stays quiet. The same ten
    significant digits used to govern the bar-content hashes too; those now cover
    every stored digit (ADR-198), so this form is the compiler's convention alone.
    """

    return format(float(value), ".10g")


def looks_numeric(token: str) -> bool:
    try:
        float(token)
    except (TypeError, ValueError):
        return False
    return True


def as_period(value: Any) -> int | None:
    """A period is an integer >= 1. Anything else is not a period."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 1 else None
    return None


def known_indicator_type(name: Any) -> str | None:
    """Registry vocabulary only: no fuzzy matching, no clever aliasing (§8)."""

    if not isinstance(name, str) or not name.strip():
        return None
    token = normalise_indicator_type(name)
    return token if token in SUPPORTED_INDICATOR_TYPES else None


def indicator_id(type_name: str, period: int | None) -> str:
    """§10.5: `<type lowercase><period literal>`, or `<type lowercase>`."""

    base = type_name.lower().replace(" ", "")
    return base if period is None else f"{base}{period}"


def condition_keys(parameters: Mapping[str, Any]) -> set[str]:
    return set(parameters) & set(CONDITION_KEYS)


def forbidden_keys(parameters: Mapping[str, Any]) -> list[str]:
    return sorted(set(parameters) & set(FORBIDDEN_PARAMETER_KEYS))


def canonical_hint(parameters: Mapping[str, Any]) -> dict[str, Any] | None:
    """§10.2: a refusal must say what the canonical form of that rule would be."""

    if not ({"indicator", "threshold"} & set(parameters)):
        return None
    hint: dict[str, Any] = {}
    if "indicator" in parameters:
        hint["left"] = parameters["indicator"]
    if "operator" in parameters:
        hint["operator"] = parameters["operator"]
    if "threshold" in parameters:
        hint["right"] = parameters["threshold"]
    if "period" in parameters:
        hint["period"] = parameters["period"]
    return hint or None


def illegal_key_detail(parameters: Mapping[str, Any], extra: list[str]) -> str:
    detail = "keys outside the canonical condition form: " + ", ".join(extra)
    hint = canonical_hint(parameters)
    if hint is not None:
        detail += "; the canonical form of this rule is " + json.dumps(
            hint, ensure_ascii=False, sort_keys=True
        )
    return detail


def parse_canonical_condition(
    parameters: Mapping[str, Any],
) -> tuple[CanonicalCondition | None, ParameterIssue | None]:
    """Read one condition; a refusal is a code plus the reason (docs/29 §10.3)."""

    extra = sorted(set(parameters) - set(CONDITION_KEYS))
    if extra:
        return None, ParameterIssue("parameter_invalid", illegal_key_detail(parameters, extra))

    missing = [key for key in ("left", "operator", "right") if key not in parameters]
    if missing:
        return None, ParameterIssue(
            "parameter_invalid",
            "the canonical condition needs " + ", ".join(f"'{key}'" for key in missing),
        )

    left = parameters["left"]
    if not isinstance(left, str) or looks_numeric(left):
        return None, ParameterIssue(
            "parameter_invalid",
            "'left' must be a column or a declared indicator name, never a number",
        )

    operator = parameters["operator"]
    if operator not in OPERATORS:
        return None, ParameterIssue(
            "parameter_invalid", "'operator' must be one of " + ", ".join(OPERATORS)
        )

    raw_right = parameters["right"]
    right_is_operand = isinstance(raw_right, str)
    if isinstance(raw_right, str):
        if looks_numeric(raw_right):
            return None, ParameterIssue(
                "parameter_invalid",
                "'right' is a threshold; write it as a JSON number, not a numeric string",
            )
        right = raw_right
    elif is_number(raw_right):
        right = number_literal(raw_right)
    else:
        return None, ParameterIssue("parameter_invalid", "'right' must be a number or a string")

    period: int | None = None
    if "period" in parameters:
        period = as_period(parameters["period"])
        if period is None:
            return None, ParameterIssue("parameter_invalid", "'period' must be an integer >= 1")

    side = parameters.get("side")
    if side is not None and side not in ("long", "short"):
        return None, ParameterIssue("parameter_invalid", "'side' must be 'long' or 'short'")

    combine = parameters.get("combine")
    if combine is not None and combine not in ("all", "any"):
        return None, ParameterIssue("parameter_invalid", "'combine' must be 'all' or 'any'")

    return (
        CanonicalCondition(
            left=left,
            operator=str(operator),
            right=right,
            period=period,
            side=str(side) if side is not None else None,
            combine=str(combine) if combine is not None else None,
            right_is_operand=right_is_operand,
        ),
        None,
    )


@dataclass
class IndicatorDeclaration:
    """One declared indicator, complete or refused, never invented."""

    declared_name: str
    type_name: str | None = None
    period: int | None = None
    period_ref: str | None = None
    input: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    generated_id: str | None = None
    source: str = ""
    rule_ids: tuple[str, ...] = ()
    problem: ParameterIssue | None = None
    referenced: bool = False
    input_declared: bool = False

    def to_dsl(self) -> dict[str, Any]:
        spec: dict[str, Any] = {"id": self.generated_id, "type": self.type_name}
        if self.period_ref is not None:
            spec["period_ref"] = self.period_ref
        elif self.period is not None:
            spec["period"] = self.period
        if self.input is not None:
            spec["input"] = self.input
        if self.params:
            spec["params"] = dict(self.params)
        return spec

    def semantic_key(self) -> tuple[Any, ...]:
        return (self.type_name, self.period, self.input, canonical_json(self.params))

    def slot_name(self) -> str:
        if self.generated_id:
            return f"indicators.{self.generated_id}"
        return f"indicators.{(self.type_name or self.declared_name).lower()}"


def read_declaration(
    declared_name: Any,
    parameters: Mapping[str, Any],
    draft_parameters: Mapping[str, Any],
    *,
    strict_keys: bool,
) -> IndicatorDeclaration:
    """Read a declaration; a refusal comes back as a declaration with a problem.

    Returning the refused declaration lets a condition that points at it report
    the same reason instead of a second, differently-worded one.
    """

    name = declared_name if isinstance(declared_name, str) else ""
    declaration = IndicatorDeclaration(declared_name=name)

    if not name.strip():
        declaration.problem = ParameterIssue(
            "parameter_invalid", "an indicator declaration needs a 'name'"
        )
        return declaration

    type_name = known_indicator_type(name)
    declaration.type_name = type_name

    allowed = set(INDICATOR_DECLARATION_KEYS) | {"period_ref"}
    extra = sorted(set(parameters) - allowed)
    if extra and strict_keys:
        declaration.problem = ParameterIssue(
            "parameter_invalid",
            "an indicator declaration accepts "
            + "/".join(INDICATOR_DECLARATION_KEYS)
            + f", not {', '.join(repr(key) for key in extra)}",
        )
        return declaration

    if "period" in parameters:
        period = as_period(parameters["period"])
        if period is None:
            declaration.problem = ParameterIssue(
                "parameter_invalid", "'period' must be an integer >= 1"
            )
            return declaration
        declaration.period = period

    if not strict_keys and "period_ref" in parameters:
        ref = parameters["period_ref"]
        if not isinstance(ref, str) or not ref.strip():
            declaration.problem = ParameterIssue(
                "parameter_invalid", "'period_ref' must be a non-empty string"
            )
            return declaration
        resolved = draft_parameters.get(ref)
        period = as_period(resolved)
        if period is None:
            declaration.problem = ParameterIssue(
                "missing_required_slot",
                f"'period_ref' names '{ref}', which the draft's parameters do not resolve "
                "to a period; the compiler will not assume one",
            )
            return declaration
        declaration.period = period
        declaration.period_ref = ref

    if "input" in parameters:
        source = parameters["input"]
        if not isinstance(source, str) or not source.strip():
            declaration.problem = ParameterIssue(
                "parameter_invalid", "'input' must be a non-empty column name"
            )
            return declaration
        declaration.input = source
        declaration.input_declared = True

    for key, value in parameters.items():
        if key in (INDICATOR_DECLARATION_KEYS + ("period_ref",)):
            continue
        declaration.params[key] = value

    if type_name is None:
        declaration.problem = ParameterIssue(
            "indicator_unmapped",
            f"'{name}' is not in the registry's indicator vocabulary",
        )
        return declaration

    if declaration.period is None:
        declaration.problem = ParameterIssue(
            "missing_required_slot",
            f"'{name}' does not say which period to use; docs/29 §12 forbids assuming one",
        )
        return declaration

    declaration.generated_id = indicator_id(type_name, declaration.period)
    return declaration


def read_numeric_parameters(
    parameters: Mapping[str, Any], allowed: tuple[str, ...], label: str
) -> tuple[dict[str, Any], ParameterIssue | None]:
    """Read a flat numeric block (`risk`); nested or unknown keys are refused."""

    values: dict[str, Any] = {}
    for key, value in parameters.items():
        if key not in allowed:
            return {}, ParameterIssue(
                "parameter_invalid", f"{label} does not accept the key '{key}'"
            )
        if not is_number(value):
            return {}, ParameterIssue(
                "parameter_invalid",
                f"{label}.{key} must be a flat number; a nested block is not canonical",
            )
        values[key] = value
    return values, None


def read_sizing_parameters(
    parameters: Mapping[str, Any],
) -> tuple[dict[str, Any], ParameterIssue | None]:
    values: dict[str, Any] = {}
    for key, value in parameters.items():
        if key not in SIZING_KEYS:
            return {}, ParameterIssue(
                "parameter_invalid", f"sizing does not accept the key '{key}'"
            )
        if key == "mode":
            if value not in SIZING_MODES:
                return {}, ParameterIssue(
                    "parameter_invalid", "'mode' must be one of " + ", ".join(SIZING_MODES)
                )
            values["mode"] = value
        elif not is_number(value):
            return {}, ParameterIssue("parameter_invalid", f"sizing.{key} must be a number")
        else:
            values[key] = value
    return values, None


def read_execution_parameters(
    parameters: Mapping[str, Any],
) -> tuple[dict[str, Any], ParameterIssue | None]:
    values: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in IGNORED_EXECUTION_KEYS:
            continue
        if key not in EXECUTION_KEYS:
            return {}, ParameterIssue(
                "parameter_invalid", f"execution does not accept the key '{key}'"
            )
        if key == "fill_model":
            if value not in FILL_MODELS:
                return {}, ParameterIssue(
                    "parameter_invalid", "'fill_model' must be one of " + ", ".join(FILL_MODELS)
                )
            values[key] = value
        elif key == "entry_order_type":
            if value not in ORDER_TYPES:
                return {}, ParameterIssue(
                    "parameter_invalid",
                    "'entry_order_type' must be one of " + ", ".join(ORDER_TYPES),
                )
            values[key] = value
        elif not is_number(value):
            return {}, ParameterIssue("parameter_invalid", f"execution.{key} must be a number")
        else:
            values[key] = value
    return values, None
