"""The two hashes of the compiler (docs/29 §15).

`draft_hash` answers "which draft is this", `compile_hash` answers "which
compiler version turned that draft into this specification". Neither is
`immutable_hash`: that one covers `(version, dsl)` and answers "was this row
edited". They must never stand in for each other (docs/29 §15.4).

The projection here is the reference implementation of §15.2, and
`backend/tests/test_compiler_contract.py` keeps an independent copy of the same
projection so the two can be cross-checked: if they ever disagree, one of them
is wrong and the contract says which.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

# §15.2: prose is excluded from the projection. Prose never decides anything
# (docs/29 §6.2), so it must not move the hash either.
PROSE_FIELDS: frozenset[str] = frozenset(
    {
        "statement",
        "note",
        "why",
        "reason",
        "phrase",
        "quote",
        "notes",
        "understanding_of_original",
        "limitations",
    }
)


def project(payload: Any) -> Any:
    """Drop the prose fields, keep every structured one (docs/29 §15.2)."""

    if isinstance(payload, Mapping):
        return {key: project(value) for key, value in payload.items() if key not in PROSE_FIELDS}
    if isinstance(payload, (list, tuple)):
        return [project(item) for item in payload]
    return payload


def canonical_json(payload: Any) -> str:
    """The one canonical serialisation used by both hashes (docs/29 §15.2)."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def draft_hash(draft_payload: Mapping[str, Any]) -> str:
    """§15.2: the draft is hashed through the prose-free projection."""

    return digest(project(draft_payload))


def compile_hash(
    compiler_version: str,
    draft_hash_value: str,
    strategy_id: Any,
    version: str,
    spec_payload: Mapping[str, Any],
) -> str:
    """§15.3: the compiler, the draft and the target — nothing else.

    Only a compiled result has one. A refusal has no `compile_hash` at all, so a
    reader can never mistake "refused" for "compiled with these values".
    """

    return digest(
        {
            "compiler_version": compiler_version,
            "draft_hash": draft_hash_value,
            "strategy": {"id": strategy_id, "version": version},
            "spec": spec_payload,
        }
    )
