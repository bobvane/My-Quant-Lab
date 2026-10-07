"""The `0016_strategy_experiments` migration must match the models it creates (ADR-174).

The chain-level guards (`tests/test_migration_revisions.py`, `tests/test_migrations_sqlite.py`)
prove that the migration *runs* and that its tables appear in FK order. They cannot see a
column that exists in the model but not in the migration (or the other way round): the test
schema comes from `Base.metadata.create_all`, so the two definitions would silently drift and
only production would notice. This self-test pins them together, in the style of
`tests/test_source_snapshot_migration.py`.

The parse is deliberately narrow: this revision only uses `op.create_table`,
`op.create_index`, `op.drop_index` and `op.drop_table` at the top level of `upgrade()` /
`downgrade()` -- no `batch_alter_table`, no `execute`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.domain.models import ExperimentResult, StrategyExperiment

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0016_strategy_experiments.py"
)
EXPECTED_REVISION = "0016_strategy_experiments"
EXPECTED_DOWN_REVISION = "0015_source_snapshots"

_INDEX_OPS = {"create_index", "drop_index"}
_READ_ONLY_OPS = {"drop_table", "drop_column", "drop_constraint", "alter_column"}


def _tree() -> ast.Module:
    return ast.parse(MIGRATION.read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() is missing from {MIGRATION.name}")


def _attr_name(node: ast.AST) -> str | None:
    return node.attr if isinstance(node, ast.Attribute) else None


def _strings(call: ast.Call) -> list[str]:
    return [
        arg.value
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]


def _column_calls(call: ast.Call) -> list[ast.Call]:
    return [
        arg for arg in call.args if isinstance(arg, ast.Call) and _attr_name(arg.func) == "Column"
    ]


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _operations(function_name: str) -> list[tuple[str, str]]:
    """`(op_name, table)` for every top-level `op.x(...)` call, in source order."""

    operations: list[tuple[str, str]] = []
    for node in _function(function_name).body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        op_name = _attr_name(call.func)
        if op_name is None:
            continue
        strings = _strings(call)
        if op_name in _INDEX_OPS:
            # `create_index` names the table positionally; `drop_index` passes it as
            # `table_name=`.
            keyword = _keyword(call, "table_name")
            table = (
                keyword.value
                if isinstance(keyword, ast.Constant)
                else (strings[1] if len(strings) > 1 else "")
            )
        else:
            table = strings[0] if strings else ""
        operations.append((op_name, table))
    return operations


def _created_columns(table: str) -> dict[str, bool]:
    """`{column_name: nullable}` for one `op.create_table(...)`."""

    for node in _function("upgrade").body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _attr_name(call.func) != "create_table" or _strings(call)[:1] != [table]:
            continue
        columns: dict[str, bool] = {}
        for column in _column_calls(call):
            name = _strings(column)[0]
            nullable = _keyword(column, "nullable")
            columns[name] = True if nullable is None else bool(getattr(nullable, "value", True))
        return columns
    raise AssertionError(f"upgrade() never creates {table}")


def _created_indexes(table: str) -> dict[str, list[str]]:
    indexes: dict[str, list[str]] = {}
    for node in _function("upgrade").body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _attr_name(call.func) != "create_index":
            continue
        strings = _strings(call)
        if len(strings) > 1 and strings[1] == table:
            columns = [
                item.value
                for arg in call.args[2:]
                if isinstance(arg, ast.List)
                for item in arg.elts
                if isinstance(item, ast.Constant)
            ]
            indexes[strings[0]] = columns
    return indexes


def _created_foreign_keys(table: str) -> dict[str, tuple[str, str | None]]:
    """`{local_column: (target_table.column, ondelete)}` for one `op.create_table(...)`."""

    for node in _function("upgrade").body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _attr_name(call.func) != "create_table" or _strings(call)[:1] != [table]:
            continue
        keys: dict[str, tuple[str, str | None]] = {}
        for arg in call.args:
            if not isinstance(arg, ast.Call) or _attr_name(arg.func) != "ForeignKeyConstraint":
                continue
            local = next(item.value for item in arg.args[0].elts if isinstance(item, ast.Constant))
            target = next(item.value for item in arg.args[1].elts if isinstance(item, ast.Constant))
            ondelete = _keyword(arg, "ondelete")
            keys[local] = (target, getattr(ondelete, "value", None))
        return keys
    raise AssertionError(f"upgrade() never creates {table}")


def test_the_revision_links_to_the_previous_head() -> None:
    literals = {
        node.target.id: node.value
        for node in _tree().body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    revision = literals["revision"].value
    assert revision == EXPECTED_REVISION
    assert len(revision) <= 32
    assert literals["down_revision"].value == EXPECTED_DOWN_REVISION
    assert literals["branch_labels"].value is None
    assert literals["depends_on"].value is None


def test_upgrade_only_creates() -> None:
    operations = _operations("upgrade")
    names = {name for name, _ in operations}
    assert names <= {"create_table", "create_index"}, names
    # The two new tables and their three indexes, and nothing else.
    assert names == {"create_table", "create_index"}
    assert [table for name, table in operations if name == "create_table"] == [
        "strategy_experiments",
        "experiment_results",
    ]
    source = MIGRATION.read_text(encoding="utf-8")
    assert ".execute(" not in source
    # 0014 / 0015 stay frozen: this revision never touches an existing table.
    assert not _READ_ONLY_OPS & names


def test_downgrade_reverses_the_upgrade_exactly() -> None:
    reverse_of = {"create_table": "drop_table", "create_index": "drop_index"}
    expected = [(reverse_of[name], table) for name, table in reversed(_operations("upgrade"))]
    assert _operations("downgrade") == expected


def test_created_columns_match_the_models() -> None:
    for model in (StrategyExperiment, ExperimentResult):
        table = model.__tablename__
        created = _created_columns(table)
        assert list(created) == list(model.__table__.columns.keys()), table
        for name, column in model.__table__.columns.items():
            assert created[name] == column.nullable, f"{table}.{name}"


def test_created_indexes_match_the_models() -> None:
    for model in (StrategyExperiment, ExperimentResult):
        table = model.__tablename__
        created = _created_indexes(table)
        declared = {
            index.name: [column.name for column in index.columns]
            for index in model.__table__.indexes
        }
        assert created == declared, table


def test_foreign_keys_match_the_models_and_keep_the_contract() -> None:
    experiments = _created_foreign_keys("strategy_experiments")
    assert experiments == {
        "strategy_version_id": ("strategy_versions.id", None),
        "series_id": ("market_data.id", None),
    }

    results = _created_foreign_keys("experiment_results")
    # `backtest_run_id` is nullable AND deliberately has no ondelete: a backtest is its
    # own artefact, so deleting an experiment must never cascade into `backtest_runs`.
    assert results == {
        "experiment_id": ("strategy_experiments.id", "CASCADE"),
        "backtest_run_id": ("backtest_runs.id", None),
    }

    assert ExperimentResult.__table__.columns["backtest_run_id"].nullable is True
    for foreign_key in ExperimentResult.__table__.columns["backtest_run_id"].foreign_keys:
        assert foreign_key.ondelete is None
