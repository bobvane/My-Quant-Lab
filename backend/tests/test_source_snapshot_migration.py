"""The snapshot migration is additive, ordered, and matches the model (ADR-163).

Two kinds of regression live here, both of which have already cost this repository a
red release:

* **Ordering.** ``0013_research_layer`` created a table before the table its foreign
  key pointed at. SQLite accepted it, PostgreSQL raised ``UndefinedTable`` and the API
  container refused to start (v1.9.8). The existing chain test compares
  ``create_table`` calls with each other; this file extends the rule to the column a
  *later* statement adds, which is exactly the shape of ``0015``.
* **Published migrations are frozen.** ``0014`` shipped in v2.0.0. If its body ever
  changes, every database that already ran it and every database created from scratch
  disagree — so the test reads its AST and asserts it is still the same single column.

The rest pins the semantics the phase promised: no place to store third-party full
text, a nullable snapshot link, and the three hashes staying separate.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain.models import AISourceSnapshot, ResearchArtifact

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
SNAPSHOT_MIGRATION = VERSIONS_DIR / "0015_source_snapshots.py"
PUBLISHED_0014 = VERSIONS_DIR / "0014_artifact_source_hash.py"

SNAPSHOT_TABLE = "ai_source_snapshots"
ARTIFACT_TABLE = "research_artifacts"
SNAPSHOT_FK = "fk_research_artifacts_snapshot_id"
SNAPSHOT_INDEX = "ix_ai_source_snapshots_url_time"

#: How each additive operation is undone. ``0015`` must not use anything else.
REVERSED_BY = {
    "create_table": "drop_table",
    "create_index": "drop_index",
    "add_column": "drop_column",
    "create_foreign_key": "drop_constraint",
}


class _Operations(ast.NodeVisitor):
    """Every ``op.x(...)`` / ``batch.x(...)`` call inside a function, in source order.

    Inside ``with op.batch_alter_table("t") as batch:`` the column operations name no
    table, so the enclosing batch supplies it — that keeps the operation list directly
    comparable between ``upgrade()`` and ``downgrade()``.
    """

    COLUMN_OPERATIONS = frozenset({"add_column", "drop_column", "alter_column"})

    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []
        self._table = ""

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            expression = item.context_expr
            if (
                isinstance(expression, ast.Call)
                and getattr(expression.func, "attr", None) == "batch_alter_table"
            ):
                previous, self._table = (
                    self._table,
                    _literal(expression.args[0] if expression.args else None),
                )
                try:
                    for statement in node.body:
                        self.visit(statement)
                finally:
                    self._table = previous
                return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in {"op", "batch"}
        ):
            target = _literal(node.args[0] if node.args else None)
            if func.attr in self.COLUMN_OPERATIONS:
                target = self._table or target
            self.operations.append((func.attr, target))
        self.generic_visit(node)


def _literal(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return ""
    return value if isinstance(value, str) else ""


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} has no {name}()")


def _operations(path: Path, name: str) -> list[tuple[str, str]]:
    visitor = _Operations()
    visitor.visit(_function(path, name))
    return visitor.operations


def _module_literals(path: Path, wanted: set[str]) -> dict[str, object]:
    found: dict[str, object] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            inner = node.targets[0]
            if isinstance(inner, ast.Name):
                target, value = inner.id, node.value
        if target in wanted and value is not None:
            found[target] = ast.literal_eval(value)
    return found


def _created_columns(path: Path, table: str) -> list[str]:
    columns: list[str] = []
    for node in ast.walk(_function(path, "upgrade")):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "create_table" or not node.args:
            continue
        if _literal(node.args[0]) != table:
            continue
        for argument in node.args[1:]:
            if isinstance(argument, ast.Call) and getattr(argument.func, "attr", None) == "Column":
                columns.append(_literal(argument.args[0] if argument.args else None))
    assert columns, f"{path.name} does not create {table}"
    return columns


# --- chain and frozen history -------------------------------------------------


def test_the_snapshot_migration_is_the_next_link_in_the_chain() -> None:
    values = _module_literals(SNAPSHOT_MIGRATION, {"revision", "down_revision"})
    assert values["revision"] == "0015_source_snapshots"
    assert values["down_revision"] == "0014_artifact_source_hash"
    assert len(str(values["revision"])) <= 32  # alembic_version.version_num on PostgreSQL


def test_the_published_0014_migration_is_still_one_additive_column() -> None:
    """A shipped migration is frozen: changing it would fork every migrated database."""

    assert _operations(PUBLISHED_0014, "upgrade") == [("add_column", ARTIFACT_TABLE)]
    assert _operations(PUBLISHED_0014, "downgrade") == [("drop_column", ARTIFACT_TABLE)]
    assert _module_literals(PUBLISHED_0014, {"revision"})["revision"] == "0014_artifact_source_hash"


# --- ordering -----------------------------------------------------------------


def test_the_snapshot_table_is_created_before_the_column_that_points_at_it() -> None:
    """The v1.9.8 lesson, in the shape this migration actually has.

    ``research_artifacts.snapshot_id`` references ``ai_source_snapshots``. Creating the
    table first is what keeps PostgreSQL from raising ``UndefinedTable`` while SQLite
    stays happy either way.
    """

    operations = _operations(SNAPSHOT_MIGRATION, "upgrade")
    create_at = next(
        index
        for index, (name, target) in enumerate(operations)
        if name == "create_table" and target == SNAPSHOT_TABLE
    )
    reference_at = next(
        index
        for index, (name, target) in enumerate(operations)
        if name == "add_column" and target == ARTIFACT_TABLE
    )
    assert create_at < reference_at, operations


def test_the_upgrade_only_adds_things() -> None:
    """Nothing existing may be dropped or rewritten by an additive phase."""

    names = [name for name, _ in _operations(SNAPSHOT_MIGRATION, "upgrade")]
    forbidden = {"drop_table", "drop_column", "drop_index", "drop_constraint", "alter_column"}
    assert not forbidden.intersection(names), names
    assert "execute" not in names, "raw SQL is not reviewable here; use the alembic API"


def test_downgrade_undoes_exactly_what_upgrade_did_in_reverse() -> None:
    upgrade = _operations(SNAPSHOT_MIGRATION, "upgrade")
    downgrade = _operations(SNAPSHOT_MIGRATION, "downgrade")

    assert [name for name, _ in downgrade] == [REVERSED_BY[name] for name, _ in reversed(upgrade)]
    assert {target for _, target in downgrade} == {target for _, target in upgrade}


# --- model parity -------------------------------------------------------------


def test_the_model_and_the_migration_create_the_same_columns() -> None:
    migrated = set(_created_columns(SNAPSHOT_MIGRATION, SNAPSHOT_TABLE))
    modelled = set(AISourceSnapshot.__table__.columns.keys())
    assert migrated == modelled, {
        "only in migration": sorted(migrated - modelled),
        "only in the model": sorted(modelled - migrated),
    }


def test_the_artifact_model_carries_the_nullable_snapshot_link() -> None:
    column = ResearchArtifact.__table__.c.snapshot_id
    assert column.nullable is True
    targets = {fk.target_fullname for fk in column.foreign_keys}
    assert targets == {f"{SNAPSHOT_TABLE}.id"}


def test_the_snapshot_row_has_no_place_for_third_party_full_text() -> None:
    """ADR-161: a snapshot is an observation, not a copy of somebody else's document."""

    columns = set(AISourceSnapshot.__table__.columns.keys())
    for forbidden in ("full_text", "content", "body", "raw_text", "raw", "blob", "payload"):
        assert forbidden not in columns, f"{forbidden!r} would turn a snapshot into a text store"
    assert "excerpt" in columns  # the bounded, policy-controlled alternative


def test_the_three_hashes_stay_distinct_concepts() -> None:
    """``source_hash`` / ``text_hash`` / ``source_snapshot_hash`` must not converge."""

    snapshot_columns = set(AISourceSnapshot.__table__.columns.keys())
    artifact_columns = set(ResearchArtifact.__table__.columns.keys())
    assert {"source_hash", "text_hash"} <= snapshot_columns
    assert {"source_hash", "text_hash"} <= artifact_columns
    assert "source_snapshot_hash" not in snapshot_columns
    assert "source_snapshot_hash" not in artifact_columns

    runtime = (Path(__file__).resolve().parents[1] / "app" / "ai" / "runtime.py").read_text(
        encoding="utf-8"
    )
    assert "source_snapshot_hash" in runtime, "the AI cache identity must stay its own name"


@pytest.mark.parametrize("name", ["upgrade", "downgrade"])
def test_the_snapshot_table_is_created_with_its_index(name: str) -> None:
    operations = _operations(SNAPSHOT_MIGRATION, name)
    index_ops = [(op, target) for op, target in operations if target == SNAPSHOT_INDEX]
    assert index_ops, operations
    assert ("create_index" if name == "upgrade" else "drop_index") in [op for op, _ in index_ops]
