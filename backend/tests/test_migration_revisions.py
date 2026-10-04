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


def _table_of(node: ast.AST) -> str:
    """``"ai_research_runs.id"`` -> ``"ai_research_runs"``; anything else -> ``""``."""

    try:
        literal = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return ""
    if isinstance(literal, str) and "." in literal:
        return literal.split(".", 1)[0]
    return ""


def _foreign_targets(node: ast.AST) -> set[str]:
    """The tables a ``create_table`` call points at through its foreign keys."""

    targets: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        attr = getattr(child.func, "attr", None)
        if attr == "ForeignKey" and child.args:
            targets.add(_table_of(child.args[0]))
        elif attr == "ForeignKeyConstraint" and len(child.args) >= 2:
            for element in getattr(child.args[1], "elts", []):
                targets.add(_table_of(element))
    targets.discard("")
    return targets


def _create_table_order(path: Path) -> list[tuple[str, set[str]]]:
    """Every ``op.create_table`` in ``upgrade()``, in source order, with its FK targets."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        return []
    created: list[tuple[str, set[str]]] = []
    for statement in upgrade.body:
        call = getattr(statement, "value", None)
        if not isinstance(call, ast.Call) or not call.args:
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create_table"):
            continue
        try:
            name = ast.literal_eval(call.args[0])
        except (ValueError, SyntaxError):  # a computed table name: nothing to check here
            continue
        assert isinstance(name, str), f"{path.name} has a create_table call without a name"
        created.append((name, _foreign_targets(call)))
    return created


def test_a_table_is_created_before_the_tables_its_foreign_keys_reference() -> None:
    """SQLite accepts a foreign key to a table that does not exist yet; PostgreSQL does not.

    Background: ``0013_research_layer`` created ``research_artifacts`` -- whose
    ``run_id`` foreign key points at ``ai_research_runs`` -- *before*
    ``ai_research_runs`` itself. SQLite created the table anyway, so the whole
    local suite was green, and only PostgreSQL refused:

        psycopg.errors.UndefinedTable: relation "ai_research_runs" does not exist

    The API container runs the migration chain at boot, so the CI smoke test and
    the release smoke test went red too -- which is how the ordering bug reached a
    tag. This test reads the chain itself, so it sees the ordering without a
    database. A reference to a table an *earlier* revision creates is fine and is
    not reported.
    """

    violations: dict[str, list[str]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        created = _create_table_order(path)
        order = [name for name, _ in created]
        for index, (name, targets) in enumerate(created):
            later = sorted(target for target in targets if target in order[index + 1 :])
            if later:
                violations[f"{path.name}:{name}"] = later
    assert not violations, (
        "a migration creates a table before the table its foreign key references; "
        "SQLite tolerates that, PostgreSQL raises UndefinedTable and the API container "
        f"refuses to start: {violations}"
    )


def _drop_order(path: Path) -> list[str]:
    """Every ``op.drop_table`` in ``downgrade()``, in source order."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    downgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        ),
        None,
    )
    if downgrade is None:
        return []
    dropped: list[str] = []
    for statement in downgrade.body:
        call = getattr(statement, "value", None)
        if not isinstance(call, ast.Call) or not call.args:
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "drop_table"):
            continue
        try:
            name = ast.literal_eval(call.args[0])
        except (ValueError, SyntaxError):
            continue
        if isinstance(name, str):
            dropped.append(name)
    return dropped


def test_a_table_is_dropped_after_the_tables_that_reference_it() -> None:
    """The same ordering rule in reverse: PostgreSQL refuses to drop a referenced table.

    ``DROP TABLE ai_research_runs`` with ``research_artifacts.run_id`` still
    pointing at it raises ``DependentObjectsStillExist``, so a downgrade has to
    drop the referencing table first. SQLite does not care, so this is checked by
    reading the chain.
    """

    violations: dict[str, list[str]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        targets = dict(_create_table_order(path))
        dropped = _drop_order(path)
        for index, name in enumerate(dropped):
            earlier = sorted(
                target for target in targets.get(name, set()) if target in dropped[:index]
            )
            if earlier:
                violations[f"{path.name}:{name}"] = earlier
    assert not violations, (
        "a migration drops a table before the table that references it; SQLite tolerates "
        f"that, PostgreSQL raises DependentObjectsStillExist: {violations}"
    )
