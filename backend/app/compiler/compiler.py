"""The deterministic Strategy Compiler core (docs/29, Step 2B).

One pure function: a draft payload plus a target strategy id/version goes in, a
`CompileResult` comes out. The same input always produces the same output, on
any machine, in any process: no clock, no randomness, no locale, no set
iteration order, no database, no network, no model.

The compiler is a formalization boundary, not a strategy invention engine. When
the draft does not say something the specification needs, the answer is a
rejection code and a `USER_REQUIRED` slot — never a plausible default (docs/29
§11, §12).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.compiler.errors import (
    COMPILER_VERSION,
    UNREACHABLE_IN_STEP_2B,
    RejectionLog,
)
from app.compiler.hashing import compile_hash, draft_hash
from app.compiler.mapping import (
    BLOCKING_UNKNOWN_FIELDS,
    DRIFTED_DERIVED_COLUMNS,
    MARKET_RULE_KEYS,
    RISK_KEYS,
    SYSTEM_MARKETS_SLOT,
    SYSTEM_TIMEFRAME_SLOT,
    SYSTEM_UNIVERSE_SLOT,
    TIMEFRAME_RULE_KEYS,
    CanonicalCondition,
    IndicatorDeclaration,
    ParameterIssue,
    known_indicator_type,
    parse_canonical_condition,
    read_declaration,
    read_execution_parameters,
    read_numeric_parameters,
    read_sizing_parameters,
)
from app.compiler.models import CompileResult, CompilerInput, DraftView
from app.data.strategy_service import parse_spec
from app.research.metrics import BARRS_PER_YEAR
from app.strategies.dsl import StrategySpec
from app.strategies.validator import (
    BASE_COLUMNS,
    KNOWN_DERIVED,
    ValidationIssue,
    validate_strategy,
)

# The engine ignores `input` when it computes ATR, so not declaring one there
# cannot change a result (docs/29 §12 asks for the decision only when it matters).
INPUT_SENSITIVE_TYPES = ("EMA", "SMA", "RSI", "MACD", "BOLLINGER")

# The stored `capability_report_json` is `CapabilityDecision.as_dict()`
# (`ai/research_schemas.py`), whose `verdict` is the server-side re-computation.
# `UNSUPPORTED` belongs to the registry's own vocabulary (`capabilities.py`) and
# never reaches the database; it is accepted here only as a tolerance
# (docs/29 §13.3, ADR-170).
_UNSUPPORTED_VERDICTS = ("NEEDS_CAPABILITY", "UNSUPPORTED")

_ENTRY_FIELDS = ("entry", "exit")
_UNKNOWN_SLOTS = {
    "timeframe": SYSTEM_TIMEFRAME_SLOT,
    "entry": "entry.long",
    "exit": "exit.long",
    "risk": "risk",
    "sizing": "sizing",
}


@dataclass
class _Slot:
    slot: str
    decided_by: str
    value: Any
    source_rule_ids: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "slot": self.slot,
            "decided_by": self.decided_by,
            "value": self.value,
            "source_rule_ids": list(self.source_rule_ids),
        }
        payload.update(self.extra)
        return payload


@dataclass
class _RuleEntry:
    """One draft rule, mapped or refused; prose stays in `statement` only."""

    rule_id: str
    field_name: str
    origin: str
    derived_from: Any
    statement: str
    decision: str = "UNMAPPED"
    targets: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    reason_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "draft_rule_id": self.rule_id,
            "field": self.field_name,
            "origin": self.origin,
            "derived_from": self.derived_from,
            "decision": self.decision,
            "targets": list(self.targets),
            "statement": self.statement,
        }


@dataclass
class _Placed:
    condition: CanonicalCondition
    left: str
    right: str
    rule_id: str


class _Build:
    """Collects everything the draft decided, then reports what it did not."""

    def __init__(self, compiler_input: CompilerInput, view: DraftView) -> None:
        self.input = compiler_input
        self.view = view
        self.log = RejectionLog()
        self.slots: dict[str, _Slot] = {}
        self.entries: list[_RuleEntry] = []
        self.unmapped_rules: list[dict[str, Any]] = []
        self.rules_by_id: dict[str, Mapping[str, Any]] = {}
        self.by_name: dict[str, list[IndicatorDeclaration]] = {}
        self.by_id: dict[str, IndicatorDeclaration] = {}
        self.ordered_declarations: list[IndicatorDeclaration] = []
        self.placed: dict[tuple[str, str], list[_Placed]] = {}
        self.combines: dict[tuple[str, str], set[str | None]] = {}
        self.timeframes: list[str] = []
        self.timeframe_rule_ids: list[str] = []
        self.target_timeframe: str | None = None
        self.risk_values: dict[str, tuple[Any, str]] = {}
        self.risk_rule_ids: list[str] = []
        self.sizing_values: dict[str, tuple[Any, str]] = {}
        self.sizing_rule_ids: list[str] = []
        self.execution_values: dict[str, tuple[Any, str]] = {}
        self.execution_rule_ids: list[str] = []
        self.extra_parameters: dict[str, tuple[Any, str]] = {}
        self.parameter_rule_ids: list[str] = []
        self.warnings: list[dict[str, Any]] = []
        self.dsl_validation: list[dict[str, Any]] = []
        self.parse_problem: str | None = None
        self.draft_hash_value = draft_hash(view.payload)

    # ---------------------------------------------------------------- slots

    def _add_slot(
        self,
        slot: str,
        decided_by: str,
        value: Any,
        source_rule_ids: tuple[str, ...] = (),
        **extra: Any,
    ) -> None:
        if slot in self.slots:
            existing = self.slots[slot]
            merged = tuple(dict.fromkeys(existing.source_rule_ids + source_rule_ids))
            existing.source_rule_ids = merged
            if extra:
                existing.extra.update(extra)
            return
        self.slots[slot] = _Slot(slot, decided_by, value, source_rule_ids, dict(extra))

    def _require_slot(
        self, slot: str, code: str, detail: str, rule_ids: tuple[str, ...] = ()
    ) -> None:
        """A value only the user can supply: a code plus an empty slot (§16.3)."""

        self.log.add(code, slot, rule_ids=rule_ids, detail=detail)
        marker = _Slot(slot, "USER_REQUIRED", None, ())
        self.slots.setdefault(slot, marker)

    def _reject_rule(self, entry: _RuleEntry, code: str, slot: str, detail: str) -> _RuleEntry:
        rule_ids = (entry.rule_id,) if entry.rule_id else ()
        self.log.add(code, slot, rule_ids=rule_ids, detail=detail)
        entry.decision = "REJECTED"
        entry.targets = []
        entry.reason_codes.append(code)
        if not entry.reason_detail:
            entry.reason_detail = detail
        return entry

    # ---------------------------------------------------------- indicators

    def _register_declaration(
        self, declaration: IndicatorDeclaration, rule_ids: tuple[str, ...] = ()
    ) -> IndicatorDeclaration:
        if declaration.problem is not None:
            self.log.add(
                declaration.problem.code,
                declaration.slot_name(),
                rule_ids=rule_ids,
                detail=declaration.problem.detail,
            )
            self.by_name.setdefault(declaration.declared_name, []).append(declaration)
            if declaration.problem.code == "missing_required_slot":
                self._require_slot(
                    declaration.slot_name(),
                    "missing_required_slot",
                    declaration.problem.detail,
                    rule_ids,
                )
            return declaration

        same_name = self.by_name.get(declaration.declared_name, [])
        clash = self.by_id.get(declaration.generated_id or "")
        if clash is None:
            clash = next(
                (item for item in same_name if item.generated_id == declaration.generated_id),
                None,
            )
        if clash is not None:
            self.log.add(
                "indicator_collision",
                declaration.slot_name(),
                rule_ids=rule_ids,
                detail=(
                    f"the draft declares '{declaration.declared_name}' "
                    f"({declaration.generated_id}) more than once; the compiler will neither "
                    "rename one of them nor merge them silently"
                ),
            )
            return clash
        self.by_name.setdefault(declaration.declared_name, []).append(declaration)
        if declaration.generated_id:
            self.by_id[declaration.generated_id] = declaration
        self.ordered_declarations.append(declaration)
        self._add_slot(
            declaration.slot_name(),
            "DRAFT",
            declaration.to_dsl(),
            rule_ids,
        )
        return declaration

    def _read_indicators(self) -> None:
        for indicator in self.view.indicators:
            parameters = indicator.get("parameters")
            declaration = read_declaration(
                indicator.get("name"),
                parameters if isinstance(parameters, Mapping) else {},
                self.view.parameters,
                strict_keys=False,
            )
            declaration.source = "draft.indicators"
            self._register_declaration(declaration)

    def _require_declaration_details(self, declaration: IndicatorDeclaration, rule_id: str) -> None:
        """Only a referenced indicator owes an input column (product decision A1)."""

        if declaration.input_declared or declaration.type_name not in INPUT_SENSITIVE_TYPES:
            return
        rule_ids = (rule_id,) if rule_id else ()
        self._require_slot(
            f"{declaration.slot_name()}.input",
            "missing_required_slot",
            (
                f"a rule uses '{declaration.declared_name}' without saying which column it is "
                "computed on; docs/29 §12 forbids assuming one"
            ),
            rule_ids,
        )

    def _forced_declaration(
        self, token: str, type_name: str, period: int | None = None
    ) -> IndicatorDeclaration:
        """A rule referenced an indicator the draft never declared (§12).

        §10.1's canonical example carries the period inside the condition, so a period
        given there is the draft's own answer and is used as one. A period nobody wrote
        down is still a question for the user, never a default.
        """

        declaration = IndicatorDeclaration(
            declared_name=token,
            type_name=type_name,
            source=f"referenced-by-rule:{token}",
            period=period,
        )
        from app.compiler.mapping import indicator_id  # local: keeps the import list short

        declaration.generated_id = indicator_id(type_name, period)
        self.by_name.setdefault(token, []).append(declaration)
        if declaration.generated_id:
            self.by_id[declaration.generated_id] = declaration
        self.ordered_declarations.append(declaration)
        if period is None:
            self._add_slot(declaration.slot_name(), "USER_REQUIRED", None, ())
            self._require_slot(
                f"{declaration.slot_name()}.period",
                "missing_required_slot",
                (
                    f"'{token}' is used but never declared, and the condition does not say "
                    "which period to use; docs/29 §12 forbids assuming one"
                ),
            )
        else:
            self._add_slot(
                declaration.slot_name(),
                "DRAFT_PARAMETER",
                declaration.to_dsl(),
                (),
            )
        if type_name in INPUT_SENSITIVE_TYPES:
            self._require_slot(
                f"{declaration.slot_name()}.input",
                "missing_required_slot",
                (
                    f"'{token}' is used but never declared, so the column it is computed on "
                    "is unknown; docs/29 §12 forbids assuming one"
                ),
            )
        return declaration

    def _resolve_operand(
        self, token: str, rule_id: str, period: int | None = None
    ) -> tuple[str | None, ParameterIssue | None]:
        declaration = self.by_id.get(token)
        if declaration is None:
            named = self.by_name.get(token, [])
            if len(named) > 1:
                ids = ", ".join(sorted(item.generated_id or "?" for item in named))
                return None, ParameterIssue(
                    "rule_conflict",
                    (
                        f"'{token}' is declared more than once ({ids}); "
                        "the condition must name the indicator id it means"
                    ),
                )
            if named:
                declaration = named[0]
        if declaration is not None:
            if declaration.problem is not None:
                return None, declaration.problem
            if (
                period is not None
                and declaration.period is not None
                and period != declaration.period
            ):
                return None, ParameterIssue(
                    "rule_conflict",
                    (
                        f"'{token}' is declared with period {declaration.period}, but the "
                        f"condition says {period}; docs/29 §10.3 refuses to pick one"
                    ),
                )
            declaration.referenced = True
            if rule_id and rule_id not in declaration.rule_ids:
                declaration.rule_ids = declaration.rule_ids + (rule_id,)
            self._require_declaration_details(declaration, rule_id)
            return declaration.generated_id, None
        if token in BASE_COLUMNS:
            return token, None
        if token in KNOWN_DERIVED:
            if token in DRIFTED_DERIVED_COLUMNS:
                # docs/29 §13.2: `validate_strategy` knows this name, but
                # `build_features` never materialises the column. Compiling it would
                # hand the engine a condition it cannot read (a silent no-op).
                return None, ParameterIssue(
                    "engine_incompatible",
                    (
                        f"'{token}' is in the validator's column vocabulary but "
                        "build_features never materialises it; docs/29 §13.2 refuses a "
                        "condition the engine cannot read"
                    ),
                )
            return token, None
        type_name = known_indicator_type(token)
        if type_name is not None:
            forced = self._forced_declaration(token, type_name, period)
            return forced.generated_id, None
        return None, ParameterIssue(
            "indicator_unmapped",
            f"'{token}' is neither a known column nor a declared indicator",
        )

    # --------------------------------------------------------------- rules

    def _read_rules(self) -> None:
        for rule in self.view.rules:
            entry = _RuleEntry(
                rule_id=rule.get("id") if isinstance(rule.get("id"), str) else "",
                field_name=rule.get("field") if isinstance(rule.get("field"), str) else "",
                origin=rule.get("origin") if isinstance(rule.get("origin"), str) else "UNKNOWN",
                derived_from=rule.get("derived_from"),
                statement=rule.get("statement") if isinstance(rule.get("statement"), str) else "",
            )
            self.entries.append(entry)
            if entry.rule_id:
                self.rules_by_id[entry.rule_id] = rule
            parameters = rule.get("parameters")
            block: Mapping[str, Any] = parameters if isinstance(parameters, Mapping) else {}
            self._dispatch(entry, block)

    def _dispatch(self, entry: _RuleEntry, parameters: Mapping[str, Any]) -> None:
        field_name = entry.field_name
        if field_name == "indicator":
            self._map_indicator_rule(entry, parameters)
        elif field_name in _ENTRY_FIELDS:
            self._map_side_rule(entry, parameters)
        elif field_name == "timeframe":
            self._map_timeframe_rule(entry, parameters)
        elif field_name == "market":
            self._map_market_rule(entry, parameters)
        elif field_name == "universe":
            self._map_universe_rule(entry, parameters)
        elif field_name == "risk":
            self._collect_rule_parameters(entry, parameters, self.risk_rule_ids)
        elif field_name == "sizing":
            self._collect_rule_parameters(entry, parameters, self.sizing_rule_ids)
        elif field_name == "execution":
            self._collect_rule_parameters(entry, parameters, self.execution_rule_ids)
        elif field_name == "parameter":
            self._collect_rule_parameters(entry, parameters, self.parameter_rule_ids)
        else:
            self._reject_rule(
                entry,
                "rule_unmapped",
                field_name or "draft",
                f"the compiler has no mapping for field '{field_name}'",
            )

    def _map_indicator_rule(self, entry: _RuleEntry, parameters: Mapping[str, Any]) -> None:
        """docs/29 §10.6: a declaration (`name`) or a condition (`left`), never both."""

        slot = "indicators"
        if not parameters:
            self._reject_rule(
                entry,
                "rule_unmapped",
                slot,
                "the rule carries no canonical parameters, so there is nothing to map",
            )
            return
        has_name = "name" in parameters
        has_left = "left" in parameters
        if has_name and has_left:
            self._reject_rule(
                entry,
                "parameter_invalid",
                slot,
                "'name' declares an indicator and 'left' compares operands; both cannot appear",
            )
            return
        if has_name:
            body = {key: value for key, value in parameters.items() if key != "name"}
            declaration = read_declaration(
                parameters.get("name"),
                body,
                self.view.parameters,
                strict_keys=True,
            )
            declaration.source = f"draft.rule:{entry.rule_id}"
            declaration.rule_ids = (entry.rule_id,) if entry.rule_id else ()
            declaration = self._register_declaration(
                declaration, (entry.rule_id,) if entry.rule_id else ()
            )
            if declaration.problem is not None:
                entry.decision = "REJECTED"
                entry.reason_codes.append(declaration.problem.code)
                entry.reason_detail = declaration.problem.detail
                return
            entry.decision = "MAPPED"
            entry.targets = [declaration.slot_name()]
            return

        if "side" in parameters:
            self._reject_rule(
                entry,
                "parameter_invalid",
                slot,
                (
                    "the canonical condition form of an indicator rule carries no 'side'; "
                    "side belongs to an entry/exit rule"
                ),
            )
            return
        condition, issue = parse_canonical_condition(parameters)
        if issue is not None:
            self._reject_rule(entry, issue.code, slot, issue.detail)
            return
        assert condition is not None
        left, problem = self._resolve_operand(condition.left, entry.rule_id, condition.period)
        if problem is not None:
            self._reject_rule(entry, problem.code, slot, problem.detail)
            return
        if condition.right_is_operand:
            right, problem = self._resolve_operand(condition.right, entry.rule_id, condition.period)
            if problem is not None:
                self._reject_rule(entry, problem.code, slot, problem.detail)
                return
        else:
            right = condition.right
        entry.decision = "MAPPED"
        entry.targets = [f"indicators.{left}"]
        self._add_slot(
            f"indicators.{left}.condition",
            "DRAFT_PARAMETER",
            {"op": condition.operator, "left": left, "right": right},
            (entry.rule_id,) if entry.rule_id else (),
        )

    def _map_side_rule(self, entry: _RuleEntry, parameters: Mapping[str, Any]) -> None:
        field_name = entry.field_name
        if not parameters:
            self._reject_rule(
                entry,
                "rule_unmapped",
                field_name,
                "the rule carries no canonical parameters, so there is nothing to map",
            )
            return
        condition, issue = parse_canonical_condition(parameters)
        if issue is not None:
            self._reject_rule(entry, issue.code, field_name, issue.detail)
            return
        assert condition is not None
        side = condition.side
        if side is None:
            self._reject_rule(
                entry,
                "parameter_invalid",
                field_name,
                f"a '{field_name}' rule must name its 'side' ('long' or 'short')",
            )
            return

        slot = f"{field_name}.{side}"
        left, problem = self._resolve_operand(condition.left, entry.rule_id, condition.period)
        if problem is None and condition.right_is_operand:
            right, problem = self._resolve_operand(condition.right, entry.rule_id, condition.period)
        else:
            right = condition.right
        if problem is not None:
            self._reject_rule(entry, problem.code, slot, problem.detail)
            return

        assert left is not None
        key = (field_name, side)
        self.placed.setdefault(key, []).append(_Placed(condition, left, right, entry.rule_id))
        self.combines.setdefault(key, set()).add(condition.combine)
        entry.decision = "MAPPED"
        entry.targets = [slot]

    def _map_timeframe_rule(self, entry: _RuleEntry, parameters: Mapping[str, Any]) -> None:
        extra = sorted(set(parameters) - set(TIMEFRAME_RULE_KEYS))
        if extra:
            self._reject_rule(
                entry,
                "parameter_invalid",
                SYSTEM_TIMEFRAME_SLOT,
                "a timeframe rule accepts only 'timeframe', not " + ", ".join(extra),
            )
            return
        value = parameters.get("timeframe")
        if not isinstance(value, str) or not value.strip():
            self._reject_rule(
                entry,
                "parameter_invalid",
                SYSTEM_TIMEFRAME_SLOT,
                "a timeframe rule must carry 'timeframe' as a non-empty string",
            )
            return
        self.timeframes.append(value)
        if entry.rule_id:
            self.timeframe_rule_ids.append(entry.rule_id)
        entry.decision = "MAPPED"
        entry.targets = [SYSTEM_TIMEFRAME_SLOT]

    def _map_market_rule(self, entry: _RuleEntry, parameters: Mapping[str, Any]) -> None:
        extra = sorted(set(parameters) - set(MARKET_RULE_KEYS))
        if extra:
            self._reject_rule(
                entry,
                "parameter_invalid",
                "market",
                "a market rule accepts only "
                + ", ".join(MARKET_RULE_KEYS)
                + ", not "
                + ", ".join(extra),
            )
            return
        entry.decision = "INFORMATION_ONLY"
        for key in ("universe", "markets"):
            if key in parameters:
                self._not_expressible(entry, key, parameters.get(key))
        if "asset_classes" in parameters:
            entry.targets.append("market.asset_classes")
        if "timeframes" in parameters:
            entry.targets.append(SYSTEM_TIMEFRAME_SLOT)

    def _map_universe_rule(self, entry: _RuleEntry, parameters: Mapping[str, Any]) -> None:
        self._not_expressible(entry, "universe", parameters.get("universe", parameters))

    def _not_expressible(self, entry: _RuleEntry, key: str, value: Any) -> None:
        """docs/29 §6.1: DSL 1.0 has no field for a market, a universe or a symbol."""

        slot = SYSTEM_UNIVERSE_SLOT if key == "universe" else SYSTEM_MARKETS_SLOT
        rendered = value if isinstance(value, (str, int, float, list)) else None
        self.log.add(
            "not_expressible",
            slot,
            rule_ids=(entry.rule_id,) if entry.rule_id else (),
            detail=(
                f"DSL 1.0 has no field for '{key}' (value: {rendered!r}); an instrument is not "
                "a strategy slot, so nothing is substituted for it"
            ),
        )
        entry.decision = "REJECTED"
        entry.reason_codes.append("not_expressible")
        if not entry.reason_detail:
            entry.reason_detail = f"'{key}' has no DSL 1.0 slot"

    def _collect_rule_parameters(
        self, entry: _RuleEntry, parameters: Mapping[str, Any], ledger: list[str]
    ) -> None:
        if not parameters:
            self._reject_rule(
                entry,
                "rule_unmapped",
                entry.field_name,
                "the rule carries no canonical parameters, so there is nothing to map",
            )
            return
        entry.decision = "MAPPED"
        entry.targets = [entry.field_name]
        if entry.rule_id:
            ledger.append(entry.rule_id)

    # ------------------------------------------------------------ unknowns

    def _read_unknowns(self) -> None:
        for unknown in self.view.unknowns:
            field_name = unknown.get("field")
            if not isinstance(field_name, str) or not field_name:
                continue
            rule_id = unknown.get("rule_id")
            needed = unknown.get("needed_to_formalize", True)
            rule_ids = (rule_id,) if isinstance(rule_id, str) and rule_id else ()
            carries_required = False
            if rule_ids:
                carrier = self.rules_by_id.get(rule_ids[0])
                if carrier is not None and carrier.get("field") in (
                    "entry",
                    "exit",
                    "risk",
                    "sizing",
                    "timeframe",
                ):
                    carries_required = True
            if not (needed and (field_name in BLOCKING_UNKNOWN_FIELDS or carries_required)):
                continue
            slot = _UNKNOWN_SLOTS.get(field_name, field_name)
            self._require_slot(
                slot,
                "unknown_blocks_slot",
                (
                    f"the draft marks '{field_name}' as unknown and it is needed to formalize "
                    "the strategy; the compiler will not fill it in"
                ),
                rule_ids,
            )

    # ---------------------------------------------------------- capability

    def _read_capability(self) -> None:
        report = self.input.capability_report
        if not report:
            return
        missing = [str(item) for item in report.get("missing") or []]
        partial = [str(item) for item in report.get("partial") or []]
        verdict = report.get("verdict")
        if missing:
            self.log.add(
                "capability_missing",
                "capability",
                detail="the capability report marks these as missing: "
                + ", ".join(sorted(missing)),
            )
        if partial:
            self.log.add(
                "capability_missing",
                "capability",
                detail=(
                    "the capability report marks these as only partially supported: "
                    + ", ".join(sorted(partial))
                ),
            )
        if verdict in _UNSUPPORTED_VERDICTS and not missing and not partial:
            self.log.add(
                "capability_missing",
                "capability",
                detail=(
                    f"the capability report's verdict is {verdict!r} and it names no "
                    "missing or partial capability"
                ),
            )

    # ---------------------------------------------------- risk / sizing / execution

    def _merge(
        self,
        target: dict[str, tuple[Any, str]],
        incoming: Mapping[str, Any],
        label: str,
        rule_id: str,
    ) -> None:
        for key, value in incoming.items():
            previous = target.get(key)
            if previous is not None and previous[0] != value:
                self.log.add(
                    "rule_conflict",
                    f"{label}.{key}",
                    rule_ids=tuple(item for item in (previous[1], rule_id) if item),
                    detail=f"two rules give '{label}.{key}' different values",
                )
                continue
            target.setdefault(key, (value, rule_id))

    def _read_risk(self) -> None:
        for rule_id in dict.fromkeys(self.risk_rule_ids):
            rule = self.rules_by_id.get(rule_id, {})
            parameters = rule.get("parameters")
            values, issue = read_numeric_parameters(
                parameters if isinstance(parameters, Mapping) else {}, RISK_KEYS, "risk"
            )
            if issue is not None:
                entry = self._entry(rule_id)
                if entry is not None:
                    self._reject_rule(entry, issue.code, "risk", issue.detail)
                continue
            self._merge(self.risk_values, values, "risk", rule_id)

        for key, (value, rule_id) in self.risk_values.items():
            self._add_slot(f"risk.{key}", "DRAFT_PARAMETER", value, (rule_id,))

        if not self.risk_values:
            empty = [entry for entry in self.entries if entry.field_name == "risk"]
            if not empty:
                self._require_slot(
                    "risk",
                    "missing_required_slot",
                    (
                        "the draft says nothing about risk; docs/29 §11 requires a stop loss or "
                        "a take profit and the engine would otherwise size every position at 100%"
                    ),
                )
            return

        has_stop = "stop_loss_atr_multiple" in self.risk_values
        has_take = any(
            key in self.risk_values
            for key in ("take_profit_r_multiple", "take_profit_atr_multiple")
        )
        if not (has_stop or has_take):
            self._require_slot(
                "risk",
                "missing_required_slot",
                (
                    "the risk block defines neither a stop loss nor a take profit; docs/29 §11 "
                    "forbids inventing a multiple"
                ),
            )
        if "max_position_pct" not in self.risk_values:
            self._require_slot(
                "risk.max_position_pct",
                "missing_required_slot",
                (
                    "the draft does not say how much of the account a position may use; the "
                    "engine's 1.0 default would deploy everything"
                ),
            )

    def _read_sizing(self) -> None:
        for rule_id in dict.fromkeys(self.sizing_rule_ids):
            rule = self.rules_by_id.get(rule_id, {})
            parameters = rule.get("parameters")
            values, issue = read_sizing_parameters(
                parameters if isinstance(parameters, Mapping) else {}
            )
            if issue is not None:
                entry = self._entry(rule_id)
                if entry is not None:
                    self._reject_rule(entry, issue.code, "sizing", issue.detail)
                continue
            self._merge(self.sizing_values, values, "sizing", rule_id)

        for key, (value, rule_id) in self.sizing_values.items():
            self._add_slot(f"sizing.{key}", "DRAFT_PARAMETER", value, (rule_id,))

        mode = self.sizing_values.get("mode", (None, ""))[0]
        if mode is None:
            self._require_slot(
                "sizing.mode",
                "missing_required_slot",
                "the draft does not say how a position is sized; docs/29 §12 forbids assuming one",
            )
            return
        needed = {
            "fixed_fraction": "fraction",
            "risk_per_trade": "risk_pct",
            "atr_risk": "atr_multiple",
        }.get(str(mode))
        if needed is not None and needed not in self.sizing_values:
            self._require_slot(
                f"sizing.{needed}",
                "missing_required_slot",
                f"sizing mode '{mode}' needs '{needed}', which the draft does not give",
            )

    def _read_execution(self) -> None:
        for rule_id in dict.fromkeys(self.execution_rule_ids):
            rule = self.rules_by_id.get(rule_id, {})
            parameters = rule.get("parameters")
            values, issue = read_execution_parameters(
                parameters if isinstance(parameters, Mapping) else {}
            )
            if issue is not None:
                entry = self._entry(rule_id)
                if entry is not None:
                    self._reject_rule(entry, issue.code, "execution", issue.detail)
                continue
            self._merge(self.execution_values, values, "execution", rule_id)

        fill_model = self.execution_values.get("fill_model")
        if fill_model is not None and fill_model[0] != "next_bar_open":
            entry = self._entry(fill_model[1])
            detail = (
                "the engine fills on the next bar's open and implements nothing else; docs/29 "
                "§13 records that as expressible but not honoured"
            )
            if entry is not None:
                self._reject_rule(entry, "engine_incompatible", "execution.fill_model", detail)
            else:
                self.log.add("engine_incompatible", "execution.fill_model", detail=detail)
        else:
            self._add_slot("execution.fill_model", "COMPILER_RULE", "next_bar_open", ())

        order_type = self.execution_values.get("entry_order_type")
        order_value = order_type[0] if order_type is not None else "market"
        order_rule_ids = (order_type[1],) if order_type is not None else ()
        self._add_slot(
            "execution.entry_order_type",
            "DRAFT_PARAMETER" if order_type is not None else "COMPILER_RULE",
            order_value,
            order_rule_ids,
        )
        if order_type is not None:
            offset_key = {"limit": "limit_offset_atr", "stop": "stop_offset_atr"}.get(
                str(order_value)
            )
            if offset_key is not None and offset_key not in self.execution_values:
                self._require_slot(
                    f"execution.{offset_key}",
                    "missing_required_slot",
                    (
                        f"entry order type '{order_value}' needs '{offset_key}', which the draft "
                        "does not give"
                    ),
                    order_rule_ids,
                )
        bars = self.execution_values.get("order_valid_bars")
        if bars is not None:
            self._add_slot("execution.order_valid_bars", "DRAFT_PARAMETER", bars[0], (bars[1],))
        else:
            self._add_slot("execution.order_valid_bars", "COMPILER_RULE", 1, ())

        for key in ("limit_offset_atr", "stop_offset_atr"):
            given = self.execution_values.get(key)
            if given is not None:
                self._add_slot(f"execution.{key}", "DRAFT_PARAMETER", given[0], (given[1],))

        for key in ("fee_bps", "slippage_bps"):
            given = self.execution_values.get(key)
            if given is not None:
                self._add_slot(f"execution.{key}", "DRAFT_PARAMETER", given[0], (given[1],))
            else:
                self._require_slot(
                    f"execution.{key}",
                    "missing_required_slot",
                    (
                        f"the draft does not say what {key} is; the engine's 0.0 default would "
                        "make the backtest free"
                    ),
                )

        capital = self.execution_values.get("initial_capital")
        if capital is not None:
            self._add_slot(
                "execution.initial_capital", "DRAFT_PARAMETER", capital[0], (capital[1],)
            )
        else:
            self._add_slot("execution.initial_capital", "COMPILER_RULE", 10_000.0, ())

    def _entry(self, rule_id: str) -> _RuleEntry | None:
        for entry in self.entries:
            if entry.rule_id == rule_id:
                return entry
        return None

    # ------------------------------------------------------------ timeframe

    def _read_timeframes(self) -> None:
        declared = [
            value for value in self.view.market.get("timeframes") or [] if isinstance(value, str)
        ]
        values = declared + [value for value in self.timeframes if value not in declared]
        rule_ids = tuple(self.timeframe_rule_ids)
        if not values:
            self._require_slot(
                SYSTEM_TIMEFRAME_SLOT,
                "unknown_blocks_slot",
                (
                    "the draft does not name a bar timeframe; docs/29 §6.1 compiles "
                    "market.timeframes[0] and §12 forbids assuming '1d'"
                ),
            )
            return
        target, ignored = values[0], values[1:]
        self.target_timeframe = target
        extra: dict[str, Any] = {}
        if ignored:
            extra["ignored"] = ignored
        self._add_slot(
            SYSTEM_TIMEFRAME_SLOT,
            "DRAFT",
            [target],
            rule_ids,
            **extra,
        )
        if target not in BARRS_PER_YEAR:
            self.log.add(
                "engine_incompatible",
                SYSTEM_TIMEFRAME_SLOT,
                rule_ids=rule_ids,
                detail=(f"'{target}' has no annualisation entry, so the engine cannot evaluate it"),
            )

    # ---------------------------------------------------------------- market

    def _read_market_block(self) -> None:
        asset_classes = [
            value for value in self.view.market.get("asset_classes") or [] if isinstance(value, str)
        ]
        if asset_classes:
            self._add_slot(
                "market.asset_classes",
                "COMPILER_RULE",
                asset_classes,
                (),
                honoured=False,
            )
        universe = self.view.market.get("universe")
        markets = self.view.market.get("markets")
        if universe:
            self.log.add(
                "not_expressible",
                SYSTEM_UNIVERSE_SLOT,
                detail=(
                    f"the draft names universe {universe!r}; DSL 1.0 has no field for it and "
                    "nothing is substituted for it"
                ),
            )
        if markets:
            self.log.add(
                "not_expressible",
                SYSTEM_MARKETS_SLOT,
                detail=(
                    "the draft names markets "
                    + ", ".join(str(item) for item in markets)
                    + "; DSL 1.0 has no field for them"
                ),
            )

    # ------------------------------------------------------------ conditions

    def _finalize_conditions(self) -> None:
        for key, items in self.placed.items():
            field_name, side = key
            slot = f"{field_name}.{side}"
            combines = self.combines.get(key, set())
            if len(items) > 1 and (None in combines or len(combines) > 1):
                rule_ids = tuple(item.rule_id for item in items if item.rule_id)
                self.log.add(
                    "rule_conflict",
                    slot,
                    rule_ids=rule_ids,
                    detail=(
                        "several rules fill this group and the draft does not say how to combine "
                        "them ('all' or 'any'), which docs/29 §10.3 forbids guessing"
                    ),
                )
                for item in items:
                    entry = self._entry(item.rule_id)
                    if entry is not None:
                        entry.decision = "REJECTED"
                        entry.reason_codes.append("rule_conflict")
                        entry.reason_detail = "this group has no unambiguous combination rule"
                continue
            combine = "all" if len(items) == 1 else sorted(combines, key=str)[0]
            assert combine in ("all", "any")
            for position, item in enumerate(items):
                self._add_slot(
                    f"{slot}.{combine}[{position}]",
                    "DRAFT_PARAMETER",
                    {"op": item.condition.operator, "left": item.left, "right": item.right},
                    (item.rule_id,) if item.rule_id else (),
                )

        for field_name in _ENTRY_FIELDS:
            if any(key[0] == field_name for key in self.placed):
                continue
            self._require_slot(
                f"{field_name}.long",
                "missing_required_slot",
                (
                    f"the draft defines no '{field_name}' rule at all, and the DSL requires at "
                    f"least one condition on {field_name}"
                ),
            )

        if any(key[0] == "entry" and key[1] == "short" for key in self.placed):
            self._require_slot(
                "market.allow_short",
                "missing_required_slot",
                (
                    "the draft contains short entry rules but never says shorting is allowed; "
                    "the DSL default is false"
                ),
            )

    # ------------------------------------------------------------- assembly

    def _assemble(self) -> StrategySpec | None:
        entry_groups = {side: self._group("entry", side) for side in ("long", "short")}
        exit_groups = {side: self._group("exit", side) for side in ("long", "short")}

        def _given(key: str, fallback: Any) -> Any:
            found = self.execution_values.get(key)
            return found[0] if found is not None else fallback

        execution: dict[str, Any] = {
            "fill_model": _given("fill_model", "next_bar_open"),
            "entry_order_type": _given("entry_order_type", "market"),
            "order_valid_bars": _given("order_valid_bars", 1),
            "initial_capital": _given("initial_capital", 10_000.0),
        }
        for key in ("limit_offset_atr", "stop_offset_atr", "fee_bps", "slippage_bps"):
            value_and_rule = self.execution_values.get(key)
            if value_and_rule is not None:
                execution[key] = value_and_rule[0]
        if self.sizing_values:
            execution["sizing"] = {
                key: value for key, (value, _rule_id) in self.sizing_values.items()
            }

        asset_classes = [
            value for value in self.view.market.get("asset_classes") or [] if isinstance(value, str)
        ]
        market: dict[str, Any] = {}
        if asset_classes:
            market["asset_classes"] = asset_classes
        if self.target_timeframe:
            market["timeframes"] = [self.target_timeframe]

        spec: dict[str, Any] = {
            "schema_version": "1.0",
            "strategy": {
                "id": str(self.input.strategy_id),
                "name": self.view.strategy_name,
                "version": self.input.version,
                "source": {
                    "type": "compiler",
                    "compiler_version": COMPILER_VERSION,
                    "draft_id": self.input.draft_id,
                    "hypothesis_id": self.input.hypothesis_id,
                    "run_id": self.input.run_id,
                },
            },
            "market": market,
            "indicators": [
                declaration.to_dsl()
                for declaration in self.ordered_declarations
                if declaration.generated_id
            ],
            "features": [],
            "parameters": dict(self.view.parameters),
            "entry": entry_groups,
            "exit": exit_groups,
            "execution": execution,
        }
        if self.risk_values:
            spec["risk"] = {key: value for key, (value, _rule_id) in self.risk_values.items()}

        try:
            return parse_spec(spec)
        except ValueError as problem:
            detail = str(problem)
            self.dsl_validation.append(
                ValidationIssue(
                    severity="error",
                    code="validation_failed",
                    message=detail,
                    path=None,
                ).as_dict()
            )
            self.parse_problem = detail
            return None

    def _group(self, field_name: str, side: str) -> dict[str, Any] | None:
        items = self.placed.get((field_name, side))
        if not items:
            return None
        combines = self.combines.get((field_name, side), set())
        if len(items) > 1 and (None in combines or len(combines) > 1):
            return None
        combine = "all" if len(items) == 1 else sorted(combines, key=str)[0]
        return {combine: [item.condition.to_dsl(item.left, item.right) for item in items]}

    def _mechanical_slots(self) -> None:
        self._add_slot("strategy.id", "COMPILER_RULE", str(self.input.strategy_id), ())
        self._add_slot("strategy.name", "DRAFT", self.view.strategy_name, ())
        self._add_slot("strategy.version", "COMPILER_RULE", self.input.version, ())
        self._add_slot(
            "strategy.source",
            "COMPILER_RULE",
            {
                "type": "compiler",
                "compiler_version": COMPILER_VERSION,
                "draft_id": self.input.draft_id,
                "hypothesis_id": self.input.hypothesis_id,
                "run_id": self.input.run_id,
            },
            (),
        )
        if self.view.parameters:
            rule_ids = tuple(dict.fromkeys(self.parameter_rule_ids))
            self._add_slot("parameters", "DRAFT", dict(self.view.parameters), rule_ids)

    # ----------------------------------------------------------------- run

    def run(self) -> CompileResult:
        self._read_indicators()
        self._read_rules()
        self._read_market_block()
        self._read_timeframes()
        self._finalize_conditions()
        self._read_unknowns()
        self._read_capability()
        self._read_risk()
        self._read_sizing()
        self._read_execution()
        self._mechanical_slots()

        spec = self._assemble()
        if spec is not None:
            report = validate_strategy(spec)
            self.dsl_validation.extend(issue.as_dict() for issue in report.issues)
            self.warnings.extend(issue.as_dict() for issue in report.warnings)
            errors = report.errors
            if errors:
                self.log.add(
                    "validation_failed",
                    "dsl",
                    detail="the static validator rejects the specification: "
                    + ", ".join(sorted({str(issue.code) for issue in errors})),
                )

        if not self.log.codes() and spec is None:
            # Nothing else was wrong, so the inability to build a specification is
            # itself the finding. When the draft was already refused, its best-effort
            # specification adds no code: the refusal is on record (docs/29 §17.2).
            self.log.add(
                "validation_failed",
                "dsl",
                detail=self.parse_problem
                or "the assembled specification is not a valid StrategySpec",
            )
        if not self.log.codes() and spec is not None:
            result = "COMPILED"
        elif self.log.has_user_decidable():
            result = "NEEDS_USER_DECISION"
        else:
            result = "REJECTED"

        compile_hash_value: str | None = None
        if result == "COMPILED" and spec is not None:
            compile_hash_value = compile_hash(
                COMPILER_VERSION,
                self.draft_hash_value,
                spec.strategy.id,
                spec.strategy.version,
                spec.model_dump(mode="json"),
            )
        else:
            spec = None

        return CompileResult(
            compiler_version=COMPILER_VERSION,
            result=result,
            spec=spec,
            report=self._report(result, compile_hash_value),
        )

    def _report(self, result: str, compile_hash_value: str | None) -> dict[str, Any]:
        for entry in self.entries:
            if entry.decision in ("UNMAPPED", "REJECTED"):
                self.unmapped_rules.append(
                    {
                        "draft_rule_id": entry.rule_id,
                        "field": entry.field_name,
                        "reason_code": entry.reason_codes[0]
                        if entry.reason_codes
                        else "rule_unmapped",
                        "detail": entry.reason_detail
                        or "the rule carries nothing the specification can hold",
                    }
                )
        return {
            "compiler_version": COMPILER_VERSION,
            "result": result,
            "draft": {
                "draft_id": self.input.draft_id,
                "hypothesis_id": self.input.hypothesis_id,
                "run_id": self.input.run_id,
                "strategy_name": self.view.strategy_name,
                "draft_hash": self.draft_hash_value,
                "compile_hash": compile_hash_value,
            },
            "target": {
                "strategy_id": self.input.strategy_id,
                "version": self.input.version,
            },
            "rules": [entry.as_dict() for entry in self.entries],
            "slots": [self.slots[name].as_dict() for name in sorted(self.slots)],
            "unmapped": self.unmapped_rules,
            "warnings": self.warnings,
            "dsl_validation": self.dsl_validation,
            "capability": dict(self.input.capability_report or {}),
            "rejections": self.log.as_list(),
            "unreachable_in_step_2b": dict(UNREACHABLE_IN_STEP_2B),
        }


def _unreadable(compiler_input: CompilerInput, problem: str) -> CompileResult:
    payload = compiler_input.draft if isinstance(compiler_input.draft, Mapping) else {}
    log = RejectionLog()
    log.add("parameter_invalid", "draft", detail=problem)
    report = {
        "compiler_version": COMPILER_VERSION,
        "result": "REJECTED",
        "draft": {
            "draft_id": compiler_input.draft_id,
            "hypothesis_id": compiler_input.hypothesis_id,
            "run_id": compiler_input.run_id,
            "strategy_name": None,
            "draft_hash": draft_hash(payload),
            "compile_hash": None,
        },
        "target": {
            "strategy_id": compiler_input.strategy_id,
            "version": compiler_input.version,
        },
        "rules": [],
        "slots": [],
        "unmapped": [],
        "warnings": [],
        "dsl_validation": [],
        "capability": dict(compiler_input.capability_report or {}),
        "rejections": log.as_list(),
        "unreachable_in_step_2b": dict(UNREACHABLE_IN_STEP_2B),
    }
    return CompileResult(
        compiler_version=COMPILER_VERSION, result="REJECTED", spec=None, report=report
    )


def compile_strategy_draft(
    draft: Any,
    strategy_id: Any,
    version: str,
    *,
    capability_report: Mapping[str, Any] | None = None,
) -> CompileResult:
    """Compile one draft into a `StrategySpec` or into a refusal (docs/29 §5).

    `draft` is either a `CompilerInput`, a persisted draft row (its
    `draft_json`/`capability_report_json` are read, never queried) or the draft
    payload itself. The call is a pure function of its arguments.
    """

    if isinstance(draft, CompilerInput):
        compiler_input = draft
    elif isinstance(draft, Mapping):
        compiler_input = CompilerInput(
            draft=draft,
            strategy_id=strategy_id,
            version=version,
            capability_report=capability_report,
        )
    else:
        compiler_input = CompilerInput.from_row(draft, strategy_id, version)
        if capability_report is not None:
            compiler_input = CompilerInput(
                draft=compiler_input.draft,
                strategy_id=compiler_input.strategy_id,
                version=compiler_input.version,
                draft_id=compiler_input.draft_id,
                hypothesis_id=compiler_input.hypothesis_id,
                run_id=compiler_input.run_id,
                capability_report=capability_report,
            )

    view, problem = DraftView.from_payload(compiler_input.draft)
    if view is None:
        return _unreadable(compiler_input, problem or "the draft payload cannot be read")
    return _Build(compiler_input, view).run()
