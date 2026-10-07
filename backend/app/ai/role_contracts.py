"""Role contracts: the single human-maintained source of AI prompts (ADR-150).

A role contract is a markdown file under ``app/ai/contracts/``. It carries a
small front-matter block (name, role, version, task types, required model
capabilities, output language) and a body that becomes part of the system
prompt. The files are the *only* place a human edits prompt text: the database
(``ai_prompts`` / ``ai_role_contracts``) is a runtime index derived from them,
and ``content_hash`` ties an audited task back to the exact file that produced
it.

Two properties are enforced here rather than trusted:

* the prompt text used at runtime is read from the file, so a prompt cannot
  quietly fork into a second copy somewhere in the code;
* a contract that *declares* a task must *define* that task's prompt, otherwise
  the loader refuses to load it (see ``parse_contract``).
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

__all__ = [
    "CONTRACTS_DIR",
    "ContractError",
    "RoleContract",
    "contract_for_role",
    "load_contracts",
    "role_contracts",
    "sync_role_contracts",
    "system_contract",
    "task_output_schemas",
]

CONTRACTS_DIR = pathlib.Path(__file__).resolve().parent / "contracts"
SYSTEM_CONTRACT_NAME = "SYSTEM"

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TASK_HEADING = re.compile(r"^##\s+Task:\s*(\S+)\s*$", re.MULTILINE)
_REQUIRED_KEYS = ("name", "role", "version")


class ContractError(ValueError):
    """A contract file is missing, malformed or internally inconsistent."""


@dataclass(frozen=True)
class RoleContract:
    """One parsed contract file."""

    name: str
    role: str
    version: str
    task_types: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    output_language: str = ""
    prompt_names: dict[str, str] = field(default_factory=dict)
    body: str = ""
    task_prompts: dict[str, str] = field(default_factory=dict)
    content_hash: str = ""
    path: str = ""

    @property
    def ref(self) -> str:
        """``NAME@version`` — what the audit trail records."""

        return f"{self.name}@{self.version}"

    @property
    def is_system(self) -> bool:
        return self.role == SYSTEM_CONTRACT_NAME

    def prompt_name_for(self, task_type: str) -> str:
        """The stable ``ai_prompts`` name for one of this contract's tasks.

        Unchanged from the pre-contract era (``signal_explain`` /
        ``backtest_explain``) so historical audit rows keep their meaning; the
        mapping is declared in the contract file instead of hard-coded.
        """

        return self.prompt_names.get(task_type, task_type)

    def system_prompt_for(self, task_type: str) -> str:
        """The full system prompt for one task: role body + that task's section."""

        task_text = self.task_prompts.get(task_type)
        if task_text is None:
            raise ContractError(f"contract {self.ref} declares no prompt for task '{task_type}'")
        return f"{self.body}\n\n{task_text}".strip()


def parse_contract(text: str, *, path: str = "<memory>") -> RoleContract:
    """Parse one contract file. Raises ``ContractError`` when it is unusable."""

    match = _FRONT_MATTER.match(text)
    if match is None:
        raise ContractError(f"{path}: missing front matter (--- block)")

    meta: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ContractError(f"{path}: front-matter line is not 'key: value': {line!r}")
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()

    for key in _REQUIRED_KEYS:
        if not meta.get(key):
            raise ContractError(f"{path}: front matter is missing '{key}'")

    body_text = text[match.end() :]

    def _items(key: str) -> tuple[str, ...]:
        raw = meta.get(key, "")
        if not raw or raw == "*":
            return ()
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    def _pairs(key: str) -> dict[str, str]:
        raw = meta.get(key, "")
        pairs: dict[str, str] = {}
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ContractError(f"{path}: '{key}' entries must be task=name, got {chunk!r}")
            left, _, right = chunk.partition("=")
            pairs[left.strip()] = right.strip()
        return pairs

    task_types = _items("task_types")
    task_prompts: dict[str, str] = {}
    role_body = body_text
    headings = list(_TASK_HEADING.finditer(body_text))
    if headings:
        role_body = body_text[: headings[0].start()]
        for index, heading in enumerate(headings):
            start = heading.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body_text)
            task_prompts[heading.group(1)] = body_text[start:end].strip()

    if task_types and not task_prompts:
        raise ContractError(
            f"{path}: declares task types {task_types} but defines no '## Task:' section"
        )
    for declared in task_types:
        if declared not in task_prompts:
            raise ContractError(f"{path}: declares task '{declared}' but defines no prompt for it")

    return RoleContract(
        name=meta["name"],
        role=meta["role"],
        version=meta["version"],
        task_types=task_types,
        required_capabilities=_items("required_capabilities"),
        output_language=meta.get("output_language", ""),
        prompt_names=_pairs("prompt_names"),
        body=role_body.strip(),
        task_prompts=task_prompts,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        path=path,
    )


@lru_cache(maxsize=4)
def load_contracts(directory: pathlib.Path | None = None) -> dict[str, RoleContract]:
    """Every contract in ``directory`` (default ``app/ai/contracts``), by role.

    Cached: the files ship with the image and do not change while it runs. Tests
    that write contracts call ``load_contracts.cache_clear()``.
    """

    root = pathlib.Path(directory) if directory is not None else CONTRACTS_DIR
    if not root.is_dir():
        raise ContractError(f"contract directory not found: {root}")

    contracts: dict[str, RoleContract] = {}
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        contract = parse_contract(text, path=str(path))
        if contract.role in contracts:
            other = contracts[contract.role].path
            raise ContractError(f"two contracts claim role {contract.role}: {path} and {other}")
        contracts[contract.role] = contract
    if SYSTEM_CONTRACT_NAME not in contracts:
        raise ContractError(f"no {SYSTEM_CONTRACT_NAME}.md in {root}")
    return contracts


def role_contracts() -> tuple[RoleContract, ...]:
    """All contracts except the system contract, ordered by name."""

    return tuple(
        contract
        for contract in sorted(load_contracts().values(), key=lambda c: c.name)
        if not contract.is_system
    )


def system_contract() -> RoleContract:
    return load_contracts()[SYSTEM_CONTRACT_NAME]


def contract_for_role(role: str) -> RoleContract:
    contract = load_contracts().get(role.upper())
    if contract is None:
        raise ContractError(f"no contract declares role '{role}'")
    return contract


def task_output_schemas() -> dict[str, dict[str, Any]]:
    """JSON schemas for the tasks whose output contract is code, not markdown.

    Imported lazily: ``app.ai.explain`` imports this module, so a module-level
    import here would be circular.
    """

    from app.ai.explain import BACKTEST_EXPLANATION_SCHEMA, PERFORMANCE_EXPLANATION_SCHEMA
    from app.ai.provider import SIGNAL_EXPLANATION_SCHEMA
    from app.ai.research_schemas import FORMALIZATION_SCHEMA, RESEARCH_SCHEMA

    return {
        "signal_explanation": SIGNAL_EXPLANATION_SCHEMA,
        "backtest_analysis": BACKTEST_EXPLANATION_SCHEMA,
        "performance_explanation": PERFORMANCE_EXPLANATION_SCHEMA,
        "strategy_research": RESEARCH_SCHEMA,
        "strategy_formalization": FORMALIZATION_SCHEMA,
    }


def sync_role_contracts(db: Session) -> list[Any]:
    """Index the contract files into ``ai_role_contracts`` (idempotent).

    Rows are keyed by ``(name, version)``. A file whose bytes changed under the
    same version keeps its row but records the new ``content_hash`` — the audit
    trail then shows that the prompt changed, which is exactly the point of
    hashing it.
    """

    from app.domain.models import AIRoleContract

    schemas = task_output_schemas()
    rows: list[Any] = []
    changed = False
    for contract in sorted(load_contracts().values(), key=lambda c: (c.name, c.version)):
        row = db.scalar(
            select(AIRoleContract).where(
                AIRoleContract.name == contract.name,
                AIRoleContract.version == contract.version,
            )
        )
        schema: dict[str, Any] | None = None
        for task_type in contract.task_types:
            schema = schemas.get(task_type)
            if schema is not None:
                break
        if row is None:
            row = AIRoleContract(name=contract.name, version=contract.version)
            db.add(row)
            changed = True
        if row.content_hash != contract.content_hash:
            row.content_hash = contract.content_hash
            changed = True
        row.role = contract.role
        row.task_types_json = list(contract.task_types)
        row.required_capabilities_json = list(contract.required_capabilities)
        row.output_language = contract.output_language or None
        row.output_schema_json = dict(schema) if schema is not None else None
        row.source_path = contract.path
        row.is_active = True
        rows.append(row)
    if changed:
        db.commit()
    return rows
