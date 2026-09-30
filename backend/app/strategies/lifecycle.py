"""Strategy lifecycle: deterministic, evidence-gated promotion and degradation.

docs/15 Phase 8 requires that ``Experimental → OOS → Paper`` is visible, that
every promotion/degradation carries evidence, and that there is **no** path
where "AI says so" upgrades a strategy. Accordingly this module:

* only reads facts the engine already recorded (validation status, completed
  backtests, walk-forward runs, paper trades);
* applies fixed thresholds, never a model;
* moves at most one stage at a time, so the roadmap stays visible;
* writes an audit event containing the evidence snapshot on every change.

``reference_signal`` and ``retired`` are manual-only: they are never applied
automatically.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.strategy_service import record_audit
from app.domain.enums import StrategyLifecycle
from app.domain.models import (
    AuditLog,
    BacktestRun,
    PaperAccount,
    PaperTrade,
    Strategy,
    StrategyVersion,
)

__all__ = [
    "LifecycleError",
    "LifecycleThresholds",
    "apply_lifecycle",
    "evaluate_lifecycle",
]

# The progression a strategy is expected to walk. ``imported`` is the default;
# ``normalized``/``validated`` are set when a version is created.
_IMPORTED = StrategyLifecycle.IMPORTED.value
_NORMALIZED = StrategyLifecycle.NORMALIZED.value
_VALIDATED = StrategyLifecycle.VALIDATED.value
_BACKTESTED = StrategyLifecycle.BACKTESTED.value
_OOS_TESTED = StrategyLifecycle.OOS_TESTED.value
_PAPER_TRADING = StrategyLifecycle.PAPER_TRADING.value
_REFERENCE_SIGNAL = StrategyLifecycle.REFERENCE_SIGNAL.value
_DEGRADED = StrategyLifecycle.DEGRADED.value
_RETIRED = StrategyLifecycle.RETIRED.value

PIPELINE: tuple[str, ...] = (
    _IMPORTED,
    _NORMALIZED,
    _VALIDATED,
    _BACKTESTED,
    _OOS_TESTED,
    _PAPER_TRADING,
)
MANUAL_ONLY: tuple[str, ...] = (_REFERENCE_SIGNAL, _RETIRED)
_TERMINAL: tuple[str, ...] = (_DEGRADED, _RETIRED)
ALL_STAGES: frozenset[str] = frozenset(stage.value for stage in StrategyLifecycle)

# User-facing grouping (docs/15: "Experimental → OOS → Paper").
STAGE_GROUP: dict[str, str] = {
    _IMPORTED: "experimental",
    _NORMALIZED: "experimental",
    _VALIDATED: "experimental",
    _BACKTESTED: "experimental",
    _OOS_TESTED: "oos",
    _PAPER_TRADING: "paper",
    _REFERENCE_SIGNAL: "reference",
    _DEGRADED: "degraded",
    _RETIRED: "retired",
}

AUTO_EVENTS = {"walk_forward_completed"}


class LifecycleError(ValueError):
    """Raised when a lifecycle transition is not supported by the evidence."""


@dataclass(frozen=True)
class LifecycleThresholds:
    """Fixed, documented gates. No model is involved in any of them."""

    min_backtest_trades: int = 10
    min_oos_windows: int = 1
    min_paper_trades: int = 10
    degrade_pnl_threshold: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _version_ids(db: Session, strategy_id: int) -> list[int]:
    return list(
        db.scalars(
            select(StrategyVersion.id).where(StrategyVersion.strategy_id == strategy_id)
        ).all()
    )


def _completed_backtests(db: Session, version_ids: list[int]) -> list[BacktestRun]:
    if not version_ids:
        return []
    return list(
        db.scalars(
            select(BacktestRun)
            .where(
                BacktestRun.strategy_version_id.in_(version_ids),
                BacktestRun.status == "completed",
            )
            .order_by(BacktestRun.id.desc())
        ).all()
    )


def _summary(run: BacktestRun) -> dict[str, Any]:
    return dict(run.result.summary_json) if run.result is not None else {}


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_evidence(db: Session, strategy: Strategy) -> dict[str, Any]:
    versions = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.id)
    ).all()
    version_ids = [v.id for v in versions]

    runs = _completed_backtests(db, version_ids)
    latest_run = runs[0] if runs else None
    latest_summary = _summary(latest_run) if latest_run else {}
    best_trades = max(
        (_as_float(_summary(run).get("number_of_trades")) or 0 for run in runs), default=0
    )

    oos_events: list[AuditLog] = []
    if version_ids:
        oos_events = list(
            db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.event_type.in_(AUTO_EVENTS),
                    AuditLog.entity_type == "strategy_version",
                    AuditLog.entity_id.in_([str(v) for v in version_ids]),
                )
                .order_by(AuditLog.id)
            ).all()
        )
    latest_oos = dict(oos_events[-1].payload_json or {}) if oos_events else {}
    oos_windows = max(
        (int((ev.payload_json or {}).get("windows", 0)) for ev in oos_events), default=0
    )

    accounts = db.scalars(select(PaperAccount).where(PaperAccount.strategy_id == strategy.id)).all()
    prefixes = [f"{strategy.id}@{v.version}" for v in versions]
    paper_trades: list[PaperTrade] = []
    if prefixes:
        paper_trades = list(
            db.scalars(select(PaperTrade).where(PaperTrade.strategy_version.in_(prefixes))).all()
        )
    realized = sum(float(t.pnl or 0) for t in paper_trades if t.exit_time is not None)

    return {
        "version_count": len(versions),
        "current_version": versions[-1].version if versions else None,
        "validation_statuses": {v.version: v.validation_status for v in versions},
        "backtest_runs": len(runs),
        "backtest_best_trades": int(best_trades),
        "backtest_latest": {
            "run_id": latest_run.id if latest_run else None,
            "total_return": latest_summary.get("total_return"),
            "max_drawdown": latest_summary.get("max_drawdown"),
            "sharpe": latest_summary.get("sharpe"),
            "number_of_trades": latest_summary.get("number_of_trades"),
        },
        "oos_runs": len(oos_events),
        "oos_windows": oos_windows,
        "oos_latest_summary": latest_oos.get("summary"),
        "paper_accounts": len(accounts),
        "paper_trades": len(paper_trades),
        "paper_realized_pnl": realized,
    }


def _gates(evidence: dict[str, Any], thresholds: LifecycleThresholds) -> dict[str, bool]:
    return {
        _NORMALIZED: evidence["version_count"] > 0,
        _VALIDATED: any(status == "valid" for status in evidence["validation_statuses"].values()),
        _BACKTESTED: (
            evidence["backtest_runs"] > 0
            and evidence["backtest_best_trades"] >= thresholds.min_backtest_trades
        ),
        _OOS_TESTED: evidence["oos_windows"] >= thresholds.min_oos_windows,
        _PAPER_TRADING: evidence["paper_trades"] >= thresholds.min_paper_trades,
    }


def evaluate_lifecycle(
    db: Session,
    strategy: Strategy,
    *,
    thresholds: LifecycleThresholds | None = None,
) -> dict[str, Any]:
    """Return the current stage plus the evidence-gated next step."""

    limits = thresholds or LifecycleThresholds()
    evidence = _collect_evidence(db, strategy)
    gates = _gates(evidence, limits)
    current = strategy.lifecycle if strategy.lifecycle in ALL_STAGES else _IMPORTED

    suggested_next: str | None = None
    blocked_reason: str | None = None
    if current in PIPELINE:
        position = PIPELINE.index(current)
        if position < len(PIPELINE) - 1:
            candidate = PIPELINE[position + 1]
            if gates.get(candidate):
                suggested_next = candidate
            else:
                blocked_reason = f"evidence for '{candidate}' is not complete yet"

    degraded = (
        current in {_OOS_TESTED, _PAPER_TRADING, _REFERENCE_SIGNAL}
        and evidence["paper_trades"] >= limits.min_paper_trades
        and evidence["paper_realized_pnl"] < limits.degrade_pnl_threshold
    )
    if degraded:
        suggested_next = _DEGRADED

    reference_eligible = gates[_PAPER_TRADING] and evidence["paper_realized_pnl"] > 0

    stages = [
        {
            "stage": stage,
            "group": STAGE_GROUP[stage],
            "reached": _stage_reached(stage, current, gates),
        }
        for stage in (*PIPELINE, *MANUAL_ONLY, _DEGRADED)
    ]

    return {
        "strategy_id": strategy.id,
        "name": strategy.name,
        "current": current,
        "current_group": STAGE_GROUP.get(current, "experimental"),
        "suggested_next": suggested_next,
        "blocked_reason": blocked_reason,
        "reference_eligible": reference_eligible,
        "degraded": degraded,
        "degrade_reason": (
            "paper trading is losing money across enough trades" if degraded else None
        ),
        "stages": stages,
        "gates": gates,
        "evidence": evidence,
        "thresholds": limits.as_dict(),
        "manual_only_stages": list(MANUAL_ONLY),
    }


def _stage_reached(stage: str, current: str, gates: dict[str, bool]) -> bool:
    if stage in _TERMINAL:
        return current == stage
    if stage in MANUAL_ONLY:
        return current == stage
    return bool(gates.get(stage, False))


def apply_lifecycle(
    db: Session,
    strategy: Strategy,
    target_stage: str,
    *,
    actor: str = "user",
    note: str | None = None,
    thresholds: LifecycleThresholds | None = None,
    evaluation: dict[str, Any] | None = None,
) -> Strategy:
    """Move ``strategy`` to ``target_stage`` if the evidence supports it."""

    if target_stage not in ALL_STAGES:
        raise LifecycleError(f"unknown lifecycle stage '{target_stage}'")
    evaluation = evaluation or evaluate_lifecycle(db, strategy, thresholds=thresholds)
    current = evaluation["current"]

    if target_stage == current:
        return strategy

    allowed = False
    if target_stage == _DEGRADED:
        allowed = True  # a person or the rule may always flag a strategy
    elif target_stage == _RETIRED:
        allowed = True  # manual retirement, never automated
    elif target_stage == _REFERENCE_SIGNAL:
        allowed = bool(evaluation["reference_eligible"])
    elif target_stage in PIPELINE:
        allowed = target_stage == evaluation["suggested_next"]

    if not allowed:
        raise LifecycleError(
            f"cannot move '{current}' -> '{target_stage}': evidence gate not satisfied"
        )

    previous = strategy.lifecycle
    strategy.lifecycle = target_stage
    record_audit(
        db,
        event_type="strategy_lifecycle_changed",
        entity_type="strategy",
        entity_id=str(strategy.id),
        action="promote" if _rank(target_stage) > _rank(previous) else "degrade",
        actor=actor,
        payload={
            "from": previous,
            "to": target_stage,
            "note": note,
            "evidence": evaluation["evidence"],
            "evaluated_at": dt.datetime.now(tz=dt.UTC).isoformat(),
        },
    )
    db.commit()
    db.refresh(strategy)
    return strategy


def _rank(stage: str) -> int:
    order = (*PIPELINE, *MANUAL_ONLY)
    try:
        return order.index(stage)
    except ValueError:
        return -1
