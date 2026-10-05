"""Input and output shapes of the deterministic compiler (docs/29 §4, §5, §16).

`CompilerInput` names exactly what the compiler may look at. `DraftView` reads
that payload as plain data, so the compiler keeps no dependency on the layer
that produced it: the gate that guarantees the payload already ran upstream
(docs/29 §4.3). `CompileResult` carries the specification (only when the result
is COMPILED) plus the frozen Compile Report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.strategies.dsl import StrategySpec


def _mapping_list(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    """Accept a JSON array of objects, reject anything else."""

    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    items: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        items.append(item)
    return tuple(items)


@dataclass(frozen=True)
class CompilerInput:
    """Everything the compiler is allowed to read (docs/29 §4.1).

    The draft is the persisted payload plus the capability report the server
    already computed. The compiler never re-derives capabilities, never loads
    the hypothesis and never opens a session.
    """

    draft: Mapping[str, Any]
    strategy_id: Any
    version: str
    draft_id: Any = None
    hypothesis_id: Any = None
    run_id: Any = None
    capability_report: Mapping[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Any, strategy_id: Any, version: str) -> CompilerInput:
        """Build the input from a persisted draft row without touching a session.

        Reading `draft_json` and `capability_report_json` from an already loaded
        row is a read, not a query: the compiler core stays a pure function.
        """

        draft = getattr(row, "draft_json", None)
        capability = getattr(row, "capability_report_json", None)
        return cls(
            draft=draft if isinstance(draft, Mapping) else {},
            strategy_id=strategy_id,
            version=version,
            draft_id=getattr(row, "id", None),
            hypothesis_id=getattr(row, "hypothesis_id", None),
            run_id=getattr(row, "run_id", None),
            capability_report=capability if isinstance(capability, Mapping) else None,
        )


@dataclass(frozen=True)
class DraftView:
    """A read-only view of a draft payload, or the reason it cannot be read."""

    payload: Mapping[str, Any]
    strategy_name: str
    market: Mapping[str, Any]
    rules: tuple[Mapping[str, Any], ...]
    unknowns: tuple[Mapping[str, Any], ...]
    indicators: tuple[Mapping[str, Any], ...]
    required_capabilities: tuple[Mapping[str, Any], ...]
    parameters: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> tuple[DraftView | None, str | None]:
        """Return the view and a problem description, never an exception."""

        if not isinstance(payload, Mapping) or not payload:
            return None, "the draft payload is empty, so there is nothing to compile"

        name = payload.get("strategy_name")
        if not isinstance(name, str) or not name.strip():
            return None, "the draft payload does not carry a usable strategy_name"

        market = payload.get("market")
        if not isinstance(market, Mapping):
            return None, "the draft payload carries no market block"

        rules = _mapping_list(payload.get("rules"))
        if rules is None:
            return None, "the draft payload carries malformed rules"
        unknowns = _mapping_list(payload.get("unknowns"))
        if unknowns is None:
            return None, "the draft payload carries malformed unknowns"
        indicators = _mapping_list(payload.get("indicators"))
        if indicators is None:
            return None, "the draft payload carries malformed indicators"
        capability_asks = _mapping_list(payload.get("required_capabilities"))
        if capability_asks is None:
            return None, "the draft payload carries malformed capability entries"

        parameters = payload.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            return None, "the draft payload carries malformed parameters"

        return (
            cls(
                payload=payload,
                strategy_name=name,
                market=market,
                rules=rules,
                unknowns=unknowns,
                indicators=indicators,
                required_capabilities=capability_asks,
                parameters=parameters,
            ),
            None,
        )


@dataclass(frozen=True)
class CompileResult:
    """The frozen outcome: a result state, an optional specification, a report."""

    compiler_version: str
    result: str
    spec: StrategySpec | None
    report: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_compiled(self) -> bool:
        return self.result == "COMPILED"

    @property
    def draft_hash(self) -> str:
        return str(self.report.get("draft", {}).get("draft_hash", ""))

    @property
    def compile_hash(self) -> str | None:
        value = self.report.get("draft", {}).get("compile_hash")
        return None if value is None else str(value)

    @property
    def codes(self) -> tuple[str, ...]:
        codes = [entry["code"] for entry in self.report.get("rejections", [])]
        return tuple(dict.fromkeys(codes))

    def as_dsl(self) -> dict[str, Any] | None:
        """The specification as the JSON document that would be persisted."""

        if self.spec is None:
            return None
        return self.spec.model_dump(mode="json")

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "spec": self.as_dsl(),
            "report": dict(self.report),
        }
