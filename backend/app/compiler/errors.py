"""The frozen vocabulary of the deterministic Strategy Compiler (docs/29 §9).

These names are contract surface: the rejection codes, the four provenance
values a decision can carry and the three result states are pinned by
`backend/tests/test_compiler_contract.py`. Never widen them here — widening
them is a documentation change first (docs/29, then ADR-167/ADR-168).

Nothing in this module decides anything: it only names what a decision may be,
so the compiler never hand-writes an ad-hoc string for a rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

COMPILER_VERSION = "1.0"

# docs/29 §9.1. The order is part of the vocabulary: `codes()` reports in it.
REJECTION_CODES: tuple[str, ...] = (
    "needs_user_decision",
    "unknown_blocks_slot",
    "ambiguous_phrase",
    "capability_missing",
    "not_expressible",
    "indicator_unmapped",
    "parameter_invalid",
    "rule_unmapped",
    "rule_conflict",
    "indicator_collision",
    "missing_required_slot",
    "validation_failed",
    "engine_incompatible",
    "provenance_invalid",
    "version_conflict",
)

# docs/29 §8.2. A code either asks a human or it does not; the split is derived
# from this one table so no call site can classify a code differently.
USER_DECIDABLE_CODES: tuple[str, ...] = (
    "needs_user_decision",
    "unknown_blocks_slot",
    "ambiguous_phrase",
    "missing_required_slot",
)

STRUCTURAL_CODES: tuple[str, ...] = tuple(
    code for code in REJECTION_CODES if code not in USER_DECIDABLE_CODES
)

# ADR-168 plus the Step 2B boundary: legal codes that the frozen input contract
# cannot reach. They are reported so nobody reads "not reported" as "forgotten".
UNREACHABLE_IN_STEP_2B: dict[str, str] = {
    "ambiguous_phrase": (
        "ambiguity data lives in the hypothesis layer, which is outside the "
        "frozen CompilerInput (docs/29 §17.2.1, ADR-168)"
    ),
    "provenance_invalid": (
        "the draft payload carries provenance already weakened and checked "
        "upstream; a pure function cannot inspect the hypothesis layer"
    ),
    "version_conflict": (
        "the uniqueness of (strategy_id, version) lives in the database; the "
        "compiler core must not read it (docs/29 §5.3)"
    ),
}

RESULTS: tuple[str, ...] = ("COMPILED", "NEEDS_USER_DECISION", "REJECTED")

DECIDED_BY: tuple[str, ...] = (
    "DRAFT",
    "DRAFT_PARAMETER",
    "COMPILER_RULE",
    "USER_REQUIRED",
)

DECISIONS: tuple[str, ...] = ("MAPPED", "UNMAPPED", "REJECTED", "INFORMATION_ONLY")


@dataclass(frozen=True)
class Rejection:
    """One triggered rejection code (docs/29 §16.6).

    `detail` may name a structural field, a slot or a registry reason. It must
    never echo the wording of a model answer (docs/29 §16.6).
    """

    code: str
    slot: str
    rule_ids: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if self.code not in REJECTION_CODES:
            raise ValueError(f"unknown rejection code: {self.code!r}")

    @property
    def user_decidable(self) -> bool:
        return self.code in USER_DECIDABLE_CODES

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "slot": self.slot,
            "rule_ids": list(self.rule_ids),
            "detail": self.detail,
            "user_decidable": self.user_decidable,
        }


@dataclass
class RejectionLog:
    """Collects every trigger, never stops at the first one (docs/29 §8.1, §9.2).

    The same code may fire on several slots; each trigger point is kept, and an
    exact `(code, slot, rule_ids)` triple is recorded once.
    """

    _entries: list[Rejection] = field(default_factory=list)
    _seen: set[tuple[str, str, tuple[str, ...]]] = field(default_factory=set)

    def add(
        self,
        code: str,
        slot: str,
        *,
        rule_ids: tuple[str, ...] = (),
        detail: str = "",
    ) -> Rejection | None:
        key = (code, slot, tuple(rule_ids))
        if key in self._seen:
            return None
        self._seen.add(key)
        entry = Rejection(code=code, slot=slot, rule_ids=tuple(rule_ids), detail=detail)
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> list[Rejection]:
        return list(self._entries)

    def codes(self) -> tuple[str, ...]:
        """Distinct codes in vocabulary order, not in trigger order."""

        fired = {entry.code for entry in self._entries}
        return tuple(code for code in REJECTION_CODES if code in fired)

    def has_user_decidable(self) -> bool:
        return any(entry.user_decidable for entry in self._entries)

    def as_list(self) -> list[dict[str, Any]]:
        return [entry.as_dict() for entry in self._entries]
