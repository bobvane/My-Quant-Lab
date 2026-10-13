"""Who still points at a row, and what deleting it would do (ADR-222).

The three delete endpoints (a strategy, a market-data series, a backtest run) each
checked references by hand, and each check missed something: the strategy one counted
only backtest runs, signals and paper accounts; the series one counted only backtest
runs; the backtest one counted nothing at all.  When the missed reference existed, the
database refused the delete *after* the endpoint had already started deleting, and the
user saw a bare 500 (``strategies.py`` said so itself in a comment).

This module is the one place that answers "what still points at this row":

* the edges come from ``Base.metadata`` — every ``ForeignKey`` whose target is the row
  itself or one of its versions — so a foreign key added in a future model cannot
  silently escape the check;
* what each edge means for a delete is an explicit business decision in
  :data:`POLICIES` (block / cascade / set-null / archive), never inferred from the
  database's ``ondelete``;
* an edge with no decision is treated as ``block`` — the conservative default — and is
  named as ``unknown`` in the answer, so a new table degrades into a refusal instead of
  a 500.

Layer rule: this is domain code.  It counts rows and builds Chinese sentences; it never
builds an HTTP response (the callers do that, from :func:`error_body`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func, inspect, or_, select, update
from sqlalchemy.orm import Session

from app.domain.models import Base, PaperTrade, StrategyVersion

#: What a reference means when its parent row is deleted.
RULE_BLOCK: Literal["block"] = "block"
RULE_CASCADE: Literal["cascade"] = "cascade"
RULE_SET_NULL: Literal["set-null"] = "set-null"
RULE_ARCHIVE: Literal["archive"] = "archive"

Rule = Literal["block", "cascade", "set-null", "archive"]

#: The entities these checks are written for.  These strings are part of the API
#: contract (``error.details.entity``), so they are not free to rename.
STRATEGY = "strategy"
SERIES = "series"
BACKTEST_RUN = "backtest_run"

#: The single error code all three endpoints use when a delete is refused.
DELETE_BLOCKED = "delete_blocked"

#: The stable ``blockers[].kind`` vocabulary from the contract.
KIND_BACKTEST = "backtest"
KIND_EXPERIMENT = "experiment"
KIND_SIGNAL = "signal"
KIND_PAPER_ACCOUNT = "paper_account"
KIND_AI_TASK = "ai_task"
KIND_DRAFT = "draft"
KIND_UNKNOWN = "unknown"
KIND_CASCADE = "cascade"  # never reported: those rows go away with the parent


@dataclass(frozen=True)
class Policy:
    """The business decision for one referencing column."""

    kind: str
    label: str
    rule: Rule


@dataclass(frozen=True)
class Edge:
    """One ``ForeignKey`` pointing at a deletable row."""

    table: str  # the referencing (child) table
    column: str  # the referencing column
    target_table: str
    target_column: str

    def describe(self) -> str:
        return f"{self.table}.{self.column} -> {self.target_table}.{self.target_column}"


#: ``(child_table, child_column) -> Policy``.  Every edge that can point at a strategy,
#: a series or a backtest run is listed — including the ones that only *look* harmless.
#: ``ai_tasks.strategy_version_id`` is the clearest example: no code path writes it
#: today, but the foreign key is real, so a non-NULL value would refuse the delete.
POLICIES: dict[tuple[str, str], Policy] = {
    # --- a strategy, and its versions ------------------------------------------------
    ("strategy_versions", "strategy_id"): Policy(KIND_CASCADE, "策略版本", RULE_CASCADE),
    ("strategy_parameters", "strategy_version_id"): Policy(KIND_CASCADE, "策略参数", RULE_CASCADE),
    ("backtest_runs", "strategy_version_id"): Policy(KIND_BACKTEST, "回测记录", RULE_BLOCK),
    ("signals", "strategy_version_id"): Policy(KIND_SIGNAL, "信号记录", RULE_BLOCK),
    ("strategy_experiments", "strategy_version_id"): Policy(
        KIND_EXPERIMENT, "策略实验", RULE_BLOCK
    ),
    ("ai_tasks", "strategy_version_id"): Policy(KIND_AI_TASK, "AI 任务", RULE_BLOCK),
    ("strategy_drafts", "compiled_strategy_version_id"): Policy(KIND_DRAFT, "研究草案", RULE_BLOCK),
    # The same account can reference the strategy directly *and* through one of its
    # versions.  Both edges carry kind ``paper_account`` and are merged into one count,
    # so an account matching both is still counted once.
    ("paper_accounts", "strategy_id"): Policy(KIND_PAPER_ACCOUNT, "模拟盘账户", RULE_BLOCK),
    ("paper_accounts", "strategy_version_id"): Policy(KIND_PAPER_ACCOUNT, "模拟盘账户", RULE_BLOCK),
    # --- a market-data series --------------------------------------------------------
    ("market_data_bars", "series_id"): Policy(KIND_CASCADE, "K 线数据", RULE_CASCADE),
    ("feature_snapshots", "series_id"): Policy(KIND_CASCADE, "特征快照", RULE_CASCADE),
    # Archiving keeps a backtest reproducible, so this reference does not refuse the
    # ordinary delete — it switches the endpoint into its archive branch (ADR-081).  A
    # hard delete (``?purge=true``) *is* refused.
    ("backtest_runs", "dataset_version_id"): Policy(KIND_BACKTEST, "回测记录", RULE_ARCHIVE),
    ("strategy_experiments", "series_id"): Policy(KIND_EXPERIMENT, "策略实验", RULE_BLOCK),
    # --- a backtest run -------------------------------------------------------------
    ("backtest_results", "backtest_run_id"): Policy(KIND_CASCADE, "回测结果", RULE_CASCADE),
    ("backtest_trades", "backtest_run_id"): Policy(KIND_CASCADE, "回测成交", RULE_CASCADE),
    ("experiment_results", "backtest_run_id"): Policy(KIND_EXPERIMENT, "策略实验", RULE_BLOCK),
    # ``ondelete="SET NULL"``: the account keeps existing, only the binding is cleared,
    # and the caller is told through ``warnings``.
    ("paper_accounts", "backtest_run_id"): Policy(KIND_PAPER_ACCOUNT, "模拟盘账户", RULE_SET_NULL),
}

#: Which columns of which table a deletable row owns.  A strategy also owns its
#: versions, which is why deleting one looks at every version reference as well.
TARGETS: dict[str, tuple[tuple[str, str], ...]] = {
    STRATEGY: (("strategies", "id"), ("strategy_versions", "id")),
    SERIES: (("market_data", "id"),),
    BACKTEST_RUN: (("backtest_runs", "id"),),
}

#: "What else can be done" — ``available`` says whether the endpoint really offers it
#: today.  It is a hint list; the frontend decides whether a button is worth showing.
_NEXT_ACTIONS: dict[str, tuple[tuple[str, bool], ...]] = {
    STRATEGY: (("manual_cleanup", True),),
    SERIES: (("archive", True), ("manual_cleanup", True)),
    BACKTEST_RUN: (("detach", False), ("manual_cleanup", True)),
}


@dataclass(frozen=True)
class Blocker:
    """One category of records standing in the way (or changing what a delete does)."""

    table: str
    kind: str
    label: str
    count: int
    rule: Rule

    def as_dict(self) -> dict[str, object]:
        return {
            "table": self.table,
            "kind": self.kind,
            "label": self.label,
            "count": self.count,
        }


@dataclass(frozen=True)
class References:
    """Everything pointing at one row, grouped by what it means for a delete."""

    entity: str
    entity_id: int
    #: ``rule == block`` — the delete is refused.
    blocking: tuple[Blocker, ...] = ()
    #: ``rule == archive`` — the endpoint archives instead of deleting, and a hard
    #: delete (``purge``) is refused.
    archivable: tuple[Blocker, ...] = ()
    #: ``rule == set-null`` — the delete goes through and the binding is cleared.
    unbinding: tuple[Blocker, ...] = ()
    #: The ``(table, column)`` pairs to clear on a successful delete.  PostgreSQL does
    #: this by itself, but SQLite enforces foreign keys only with ``PRAGMA
    #: foreign_keys`` (which the application never sets), so the endpoint clears them as
    #: well and the 200 ``warnings`` stay true on both dialects.
    unbinding_columns: tuple[tuple[str, str], ...] = ()
    #: References that are not foreign keys at all (string version labels).  They never
    #: refuse a delete; they are reported so the user knows what changes silently.
    loose: tuple[str, ...] = ()
    #: Extra Chinese sentences for the 200 ``warnings`` list.
    warnings: tuple[str, ...] = ()

    @property
    def refusing(self) -> tuple[Blocker, ...]:
        """The groups named in a refusal: the blockers plus the archive-only ones."""

        return self.blocking + self.archivable

    @property
    def refuses_delete(self) -> bool:
        return bool(self.blocking)

    @property
    def refuses_hard_delete(self) -> bool:
        return bool(self.blocking or self.archivable)

    def warning_list(self) -> list[str]:
        return list(self.warnings)


@dataclass
class _Group:
    """A ``(table, kind)`` bucket being counted for one delete."""

    policy: Policy
    conditions: list = field(default_factory=list)
    #: ``(table, column)`` pairs merged into this bucket, kept so a ``set-null`` bucket
    #: can be cleared by name.
    columns: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# The foreign-key graph
# --------------------------------------------------------------------------------------
def _table(name: str):
    table = Base.metadata.tables.get(name)
    if table is None:  # pragma: no cover - a typo in TARGETS would land here
        raise KeyError(f"unknown table in the reference graph: {name}")
    return table


def target_columns(entity: str) -> tuple[tuple[str, str], ...]:
    """The ``(table, column)`` pairs whose foreign keys point at this entity."""

    try:
        return TARGETS[entity]
    except KeyError:  # pragma: no cover - callers pass the module constants
        raise ValueError(f"no reference target defined for {entity!r}") from None


def inbound_edges(entity: str) -> tuple[Edge, ...]:
    """Every foreign key targeting this entity, read from the model metadata."""

    targets = set(target_columns(entity))
    edges: list[Edge] = []
    for table in Base.metadata.sorted_tables:
        for foreign_key in table.foreign_keys:
            target = foreign_key.column
            if (target.table.name, target.name) not in targets:
                continue
            edges.append(
                Edge(
                    table=table.name,
                    column=foreign_key.parent.name,
                    target_table=target.table.name,
                    target_column=target.name,
                )
            )
    return tuple(sorted(edges, key=lambda edge: (edge.table, edge.column)))


def classify(edge: Edge) -> Policy:
    """The policy for ``edge``; anything unlisted is a conservative block."""

    policy = POLICIES.get((edge.table, edge.column))
    if policy is not None:
        return policy
    return Policy(KIND_UNKNOWN, f"未归类的引用（{edge.table}.{edge.column}）", RULE_BLOCK)


def edges_without_policy(entity: str) -> tuple[str, ...]:
    """Edges the policy table forgot — the reverse guard wants this to stay empty."""

    return tuple(
        edge.describe()
        for edge in inbound_edges(entity)
        if (edge.table, edge.column) not in POLICIES
    )


def policy_columns_missing_from_metadata() -> tuple[str, ...]:
    """Non-cascade policies that name a column the model metadata does not have."""

    problems: list[str] = []
    for (table_name, column_name), policy in POLICIES.items():
        if policy.rule == RULE_CASCADE:
            continue
        table = Base.metadata.tables.get(table_name)
        if table is None or column_name not in table.c:
            problems.append(f"{table_name}.{column_name}")
    return tuple(sorted(problems))


def cascade_children(entity: str) -> tuple[str, ...]:
    """Columns whose rows disappear with the parent (never named as blockers)."""

    return tuple(
        edge.describe() for edge in inbound_edges(entity) if classify(edge).rule == RULE_CASCADE
    )


# --------------------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------------------
def _target_values(db: Session, entity: str, entity_id: int) -> dict[tuple[str, str], list[int]]:
    """The values a referencing column must hold to point at this row."""

    if entity == STRATEGY:
        version_ids = list(
            db.scalars(select(StrategyVersion.id).where(StrategyVersion.strategy_id == entity_id))
        )
        return {
            ("strategies", "id"): [entity_id],
            ("strategy_versions", "id"): version_ids,
        }
    if entity == SERIES:
        return {("market_data", "id"): [entity_id]}
    if entity == BACKTEST_RUN:
        return {("backtest_runs", "id"): [entity_id]}
    raise ValueError(f"no reference target defined for {entity!r}")


def _count_groups(
    db: Session, entity: str, entity_id: int
) -> tuple[list[Blocker], list[tuple[str, str]]]:
    values = _target_values(db, entity, entity_id)
    groups: dict[tuple[str, str], _Group] = {}
    order: list[tuple[str, str]] = []

    for edge in inbound_edges(entity):
        selector = values.get((edge.target_table, edge.target_column))
        if selector is None:
            continue
        policy = classify(edge)
        if policy.rule == RULE_CASCADE:
            continue
        child = _table(edge.table)
        key = (edge.table, policy.kind)
        group = groups.get(key)
        if group is None:
            group = _Group(policy=policy)
            groups[key] = group
            order.append(key)
        group.conditions.append(child.c[edge.column].in_(selector))
        group.columns.append((edge.table, edge.column))

    blockers: list[Blocker] = []
    for key in order:
        group = groups[key]
        table = _table(key[0])
        primary_key = next(iter(inspect(table).primary_key.columns))
        statement = (
            select(func.count(func.distinct(primary_key)))
            .select_from(table)
            .where(or_(*group.conditions))
        )
        count = int(db.scalar(statement) or 0)
        if count:
            blockers.append(
                Blocker(
                    table=key[0],
                    kind=group.policy.kind,
                    label=group.policy.label,
                    count=count,
                    rule=group.policy.rule,
                )
            )
    set_null_columns = [
        pair
        for key in order
        if groups[key].policy.rule == RULE_SET_NULL
        for pair in groups[key].columns
    ]
    return blockers, set_null_columns


def loose_references(db: Session, entity: str, entity_id: int) -> list[str]:
    """References that are not foreign keys, so the graph above cannot see them.

    Only one exists today: ``paper_trades.strategy_version`` stores a version *label*
    like ``"7@1.0"`` (``backend/app/strategies/lifecycle.py``), which both databases
    treat as plain text.  Deleting the strategy leaves the trades in place, so this is
    a warning, never a blocker.
    """

    if entity != STRATEGY:
        return []
    version_labels = list(
        db.scalars(select(StrategyVersion.version).where(StrategyVersion.strategy_id == entity_id))
    )
    if not version_labels:
        return []
    prefixes = [f"{entity_id}@{label}" for label in version_labels]
    count = int(
        db.scalar(
            select(func.count())
            .select_from(PaperTrade)
            .where(PaperTrade.strategy_version.in_(prefixes))
        )
        or 0
    )
    if not count:
        return []
    return [
        f"有 {count} 笔模拟成交记录的版本号指向这个策略（字符串引用，不是外键）："
        "删掉策略不会删掉这些流水，但它们会失去对应的版本。"
    ]


def collect(db: Session, *, entity: str, entity_id: int) -> References:
    """Count everything pointing at ``entity_id`` and group it by delete semantics."""

    groups, set_null_columns = _count_groups(db, entity, entity_id)
    return References(
        entity=entity,
        entity_id=entity_id,
        blocking=tuple(group for group in groups if group.rule == RULE_BLOCK),
        archivable=tuple(group for group in groups if group.rule == RULE_ARCHIVE),
        unbinding=tuple(group for group in groups if group.rule == RULE_SET_NULL),
        unbinding_columns=tuple(set_null_columns),
        loose=tuple(loose_references(db, entity, entity_id)),
        warnings=tuple(_unbinding_warning(group) for group in groups if group.rule == RULE_SET_NULL)
        + tuple(loose_references(db, entity, entity_id)),
    )


def apply_unbindings(db: Session, report: References) -> int:
    """Clear the ``set-null`` bindings this report found, and say how many rows moved.

    ``ondelete="SET NULL"`` is the database's job — but only PostgreSQL does it
    unconditionally.  SQLite enforces foreign keys only while ``PRAGMA foreign_keys``
    is on, and the application never turns it on (``backend/app/core/db.py``: no pragma
    listener), so without this the page would promise "绑定会被清空" and leave a
    dangling ``paper_accounts.backtest_run_id`` behind on SQLite.  The endpoints call
    this before deleting the row they are about to delete, which makes the two dialects
    agree and keeps the count in ``warnings`` equal to the rows that really changed.
    """

    if not report.unbinding_columns:
        return 0
    values = _target_values(db, report.entity, report.entity_id)
    edges = {(edge.table, edge.column): edge for edge in inbound_edges(report.entity)}
    changed = 0
    for table_name, column_name in report.unbinding_columns:
        edge = edges.get((table_name, column_name))
        if edge is None:  # pragma: no cover - the pair came from that same graph
            continue
        selector = values.get((edge.target_table, edge.target_column))
        if not selector:
            continue
        table = _table(table_name)
        column = table.c[column_name]
        result = db.execute(update(table).where(column.in_(selector)).values({column: None}))
        changed += int(result.rowcount or 0)
    return changed


# --------------------------------------------------------------------------------------
# Sentences and response bodies
# --------------------------------------------------------------------------------------
def _unbinding_warning(blocker: Blocker) -> str:
    return (
        f"有 {blocker.count} 个{blocker.label}原本绑定这条记录，"
        f"删除后绑定会被清空，{blocker.label}本身保留。"
    )


def _phrase(blockers: Sequence[Blocker]) -> str:
    return "、".join(f"{blocker.count} 条{blocker.label}" for blocker in blockers)


def refusal_message(references: References) -> str:
    """The Chinese sentence shown for a refused delete, per entity."""

    detail = _phrase(references.refusing)
    if references.entity == STRATEGY:
        return f"此策略被 {detail} 关联，不能直接删除。请先删除相关记录。"
    if references.entity == SERIES:
        return (
            f"这条行情数据被 {detail} 使用，不能直接删除。"
            "请先删除相关记录，或改为归档（不带 purge 参数）。"
        )
    return f"这次回测被 {detail} 引用，不能直接删除。请先删除相关记录。"


def archive_message(symbol: str, blocking_runs: int) -> str:
    return (
        f"{symbol} 的行情数据被 {blocking_runs} 次回测使用，已归档并在行情同步中隐藏；"
        "数据保留，回测结果仍可复现。"
    )


def next_actions(entity: str) -> tuple[dict[str, object], ...]:
    return tuple(
        {"action": action, "available": available}
        for action, available in _NEXT_ACTIONS.get(entity, (("manual_cleanup", True),))
    )


def error_body(references: References, message: str) -> dict[str, object]:
    """The 409 envelope the three endpoints share (top level holds only ``error``)."""

    return {
        "error": {
            "code": DELETE_BLOCKED,
            "message": message,
            "details": {
                "entity": references.entity,
                "id": references.entity_id,
                "blockers": [blocker.as_dict() for blocker in references.refusing],
                "next": list(next_actions(references.entity)),
            },
        }
    }
