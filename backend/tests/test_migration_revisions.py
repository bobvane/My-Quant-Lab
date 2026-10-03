"""Alembic revision ids must fit the column Alembic stores them in (ADR-064).

Background: revision ``0008_github_source_pending_review`` is 33 characters, but
Alembic creates ``alembic_version.version_num`` as ``VARCHAR(32)`` on PostgreSQL.
Applying that migration raised

    psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)

inside ``UPDATE alembic_version SET version_num='0008_github_source_pending_review'``,
so the API container refused to start on a Postgres deployment and the release's
CI went red. Every local run migrates SQLite, which has no such limit, so nothing
caught it before the tag was pushed -- hence a test that reads the chain itself.

These tests need no database at all, so they run everywhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# What Alembic creates on PostgreSQL; exceed it and only PostgreSQL fails.
ALEMBIC_VERSION_COLUMN = 32


def _literals(path: Path) -> dict[str, object]:
    """Return the module-level ``revision``/``down_revision`` values of a migration."""

    found: dict[str, object] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        if target in {"revision", "down_revision"} and value is not None:
            found[target] = ast.literal_eval(value)
    return found


def _chain() -> dict[str, dict[str, object]]:
    chain: dict[str, dict[str, object]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        values = _literals(path)
        revision = values.get("revision")
        assert isinstance(revision, str), f"{path.name} declares no revision id"
        assert revision not in chain, f"duplicate revision id {revision!r} ({path.name})"
        chain[revision] = {"file": path.name, "down_revision": values.get("down_revision")}
    return chain


def test_versions_directory_is_not_empty() -> None:
    assert _chain(), f"no migrations found in {VERSIONS_DIR}"


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    too_long = {
        revision: entry["file"]
        for revision, entry in _chain().items()
        if len(revision) > ALEMBIC_VERSION_COLUMN
    }
    assert not too_long, (
        f"revision ids longer than {ALEMBIC_VERSION_COLUMN} characters cannot be stored in "
        f"alembic_version.version_num on PostgreSQL: {too_long}"
    )


def test_every_down_revision_resolves_to_a_migration() -> None:
    chain = _chain()
    dangling = {
        revision: entry["down_revision"]
        for revision, entry in chain.items()
        if entry["down_revision"] is not None and entry["down_revision"] not in chain
    }
    assert not dangling, f"down_revision points at a migration that does not exist: {dangling}"


def test_the_chain_has_exactly_one_head() -> None:
    chain = _chain()
    referenced = {
        entry["down_revision"] for entry in chain.values() if entry["down_revision"] is not None
    }
    heads = sorted(set(chain) - referenced)
    assert len(heads) == 1, f"expected exactly one head, found {heads}"
