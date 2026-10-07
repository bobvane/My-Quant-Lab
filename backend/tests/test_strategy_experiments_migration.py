"""The strategy-experiment migrations must match the models they create and extend.

ADR-174 created ``strategy_experiments`` / ``experiment_results`` in revision
``0016_strategy_experiments``; ADR-182/ADR-183 added the lifecycle and frozen-snapshot
columns in ``0019_experiment_lifecycle``. The chain-level guards
(``tests/test_migration_revisions.py``, ``tests/test_migrations_sqlite.py``) prove that the
migrations *run* and that their tables appear in FK order. They cannot see a column that
exists in the model but not in a migration (or the other way round): the test schema comes
from ``Base.metadata.create_all``, so the two definitions would silently drift and only
production would notice. This self-test pins them together, in the style of
``tests/test_source_snapshot_migration.py``.

Two rules are enforced here. ``0016`` is **frozen**: its column list is a literal below,
so a column can never be added to the old revision after the fact. ``0019`` may only
**add** nullable columns to ``strategy_experiments``, and together the two revisions must
produce exactly the columns the models declare -- in the order the models declare them.

The parse is deliberately narrow: these revisions only use ``op.create_table``,
``op.create_index``, ``op.drop_index``, ``op.drop_table``, ``op.add_column`` and
``op.drop_column`` at the top level -- no ``batch_alter_table``, no ``execute``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.domain.models import ExperimentResult, StrategyExperiment

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "0016_strategy_experiments.py"
LIFECYCLE_MIGRATION = VERSIONS / "0019_experiment_lifecycle.py"
EXPECTED_REVISION = "0016_strategy_experiments"
EXPECTED_DOWN_REVISION = "0015_source_snapshots"
EXPECTED_LIFECYCLE_REVISION = "0019_experiment_lifecycle"
EXPECTED_LIFECYCLE_DOWN_REVISION = "0018_run_progress_paper_binding"

# The columns 0016 created, frozen: the lifecycle revision is the only place new columns
# may appear, so a later edit to the old migration is caught here.
_FROZEN_0016_NAMES = {
    "strategy_experiments": [
        "id",
        "name",
        "notes",
        "status",
        "kind",
        "strategy_version_id",
        "series_id",
        "symbol",
        "timeframe",
        "parameters_json",
        "request_json",
        "summary_json",
        "error_message",
        "created_at",
        "started_at",
        "completed_at",
    ],
    "experiment_results": [
        "id",
        "experiment_id",
        "kind",
        "label",
        "parameters_json",
        "backtest_run_id",
        "metrics_json",
        "payload_json",
        "created_at",
    ],
}

# The lifecycle columns, in the order 0019 adds them (which is the order the model declares
# them). `updated_at` is maintained by the ORM; the other four are written once.
_EXPECTED_ADDED = ["updated_at", "archived_at", "initial_capital", "start_date", "end_date"]

_INDEX_OPS = {"create_index", "drop_index"}
_READ_ONLY_OPS = {"drop_table", "drop_column", "drop_constraint", "alter_column"}


def _tree(path: Path = MIGRATION) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(name: str, path: Path = MIGRATION) -> ast.FunctionDef:
    for node in _tree(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() is missing from {path.name}")


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


def _top_level_calls(function_name: str, path: Path = MIGRATION) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in _function(function_name, path).body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            calls.append(node.value)
    return calls


def _operations(function_name: str, path: Path = MIGRATION) -> list[tuple[str, str]]:
    """`(op_name, table)` for every top-level `op.x(...)` call, in source order."""

    operations: list[tuple[str, str]] = []
    for call in _top_level_calls(function_name, path):
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


def _nullable(column: ast.Call) -> bool:
    nullable = _keyword(column, "nullable")
    return True if nullable is None else bool(getattr(nullable, "value", True))


def _created_columns(table: str, path: Path = MIGRATION) -> dict[str, bool]:
    """`{column_name: nullable}` for one `op.create_table(...)`."""

    for call in _top_level_calls("upgrade", path):
        if _attr_name(call.func) != "create_table" or _strings(call)[:1] != [table]:
            continue
        return {_strings(column)[0]: _nullable(column) for column in _column_calls(call)}
    raise AssertionError(f"upgrade() never creates {table}")


def _added_columns(table: str, path: Path = LIFECYCLE_MIGRATION) -> dict[str, bool]:
    """`{column_name: nullable}` for `op.add_column(table, sa.Column(...))`, in order."""

    columns: dict[str, bool] = {}
    for call in _top_level_calls("upgrade", path):
        if _attr_name(call.func) != "add_column" or _strings(call)[:1] != [table]:
            continue
        for column in _column_calls(call):
            columns[_strings(column)[0]] = _nullable(column)
    return columns


def _dropped_columns(path: Path = LIFECYCLE_MIGRATION) -> list[str]:
    """The column names `downgrade()` drops, in source order."""

    names: list[str] = []
    for call in _top_level_calls("downgrade", path):
        if _attr_name(call.func) != "drop_column":
            continue
        strings = _strings(call)
        if len(strings) > 1:
            names.append(strings[1])
    return names


def _created_indexes(table: str) -> dict[str, list[str]]:
    indexes: dict[str, list[str]] = {}
    for call in _top_level_calls("upgrade"):
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

    for call in _top_level_calls("upgrade"):
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


def _revision_literals(path: Path) -> dict[str, ast.AST]:
    return {
        node.target.id: node.value
        for node in _tree(path).body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


# --- 0016: the tables the experiment layer was born with ---------------------------


def test_the_revision_links_to_the_previous_head() -> None:
    literals = _revision_literals(MIGRATION)
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
    # 0014 / 0015 / 0016 itself stay frozen: this revision never touches an existing table.
    assert not _READ_ONLY_OPS & names


def test_downgrade_reverses_the_upgrade_exactly() -> None:
    reverse_of = {"create_table": "drop_table", "create_index": "drop_index"}
    expected = [(reverse_of[name], table) for name, table in reversed(_operations("upgrade"))]
    assert _operations("downgrade") == expected


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


# --- 0019: the lifecycle and snapshot columns --------------------------------------


def test_the_lifecycle_revision_links_to_the_experiments_head() -> None:
    literals = _revision_literals(LIFECYCLE_MIGRATION)
    revision = literals["revision"].value
    assert revision == EXPECTED_LIFECYCLE_REVISION
    assert len(revision) <= 32
    assert literals["down_revision"].value == EXPECTED_LIFECYCLE_DOWN_REVISION
    assert literals["branch_labels"].value is None
    assert literals["depends_on"].value is None


def test_the_lifecycle_upgrade_only_adds_nullable_columns() -> None:
    operations = _operations("upgrade", LIFECYCLE_MIGRATION)
    assert [name for name, _ in operations] == ["add_column"] * len(_EXPECTED_ADDED)
    assert {table for _, table in operations} == {"strategy_experiments"}

    added = _added_columns("strategy_experiments")
    assert list(added) == _EXPECTED_ADDED
    # Every added column is nullable: existing rows predate them and must stay valid on
    # both SQLite and PostgreSQL without a backfill.
    assert set(added.values()) == {True}

    source = LIFECYCLE_MIGRATION.read_text(encoding="utf-8")
    assert ".execute(" not in source
    # No server-side default expression: the ORM owns these values, and a complex
    # `server_default` is not portable across the two supported engines.
    assert "server_default" not in source
    # The index that serves the history listing already exists; this revision adds none.
    assert "create_index" not in source


def test_the_lifecycle_downgrade_drops_the_added_columns_in_reverse() -> None:
    assert _dropped_columns() == list(reversed(_EXPECTED_ADDED))
    assert _dropped_columns() == list(reversed(list(_added_columns("strategy_experiments"))))


def test_the_two_revisions_together_match_the_models() -> None:
    for model in (StrategyExperiment, ExperimentResult):
        table = model.__tablename__
        created = _created_columns(table)
        # 0016 is frozen: its column list, in order, is a literal above.
        assert list(created) == _FROZEN_0016_NAMES[table], table
        for name in _EXPECTED_ADDED:
            assert name not in created, f"{table}.{name} must be added by 0019, not 0016"

        added = _added_columns(table)
        # Exactly the model's columns, in the model's order, split across the two
        # revisions -- a column present in one and missing from the other fails here.
        assert list(created) + list(added) == list(model.__table__.columns.keys()), table
        for name, column in model.__table__.columns.items():
            declared = (created | added)[name]
            assert declared == column.nullable, f"{table}.{name}"


def test_the_snapshot_columns_are_nullable_so_older_rows_stay_readable() -> None:
    # A row created before this revision has no snapshot values, and reading it back must
    # not require a backfill.
    columns = StrategyExperiment.__table__.columns
    for name in ("initial_capital", "start_date", "end_date", "archived_at"):
        assert columns[name].nullable is True, name
