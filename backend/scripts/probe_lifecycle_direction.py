"""Offline probe: does the audit trail name the move it recorded? (ADR-063)

Moves real strategies through the real `apply_lifecycle` against an in-memory
SQLite database and prints the `action` each move wrote to the audit log.
Retirement used to be recorded as a promotion, because the label came from
comparing ranks and `retired` sits at the top of that order while `degraded` is
in no order at all.

    python scripts/probe_lifecycle_direction.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.db import Base  # noqa: E402
from app.domain.models import (  # noqa: E402
    Asset,
    AuditLog,
    BacktestResult,
    BacktestRun,
    MarketDataSeries,
    MarketDataSource,
    PaperAccount,
    PaperTrade,
    Strategy,
    StrategyVersion,
)
from app.strategies.lifecycle import (  # noqa: E402
    LIFECYCLE_ACTIONS,
    apply_lifecycle,
    evaluate_lifecycle,
)

SAMPLE_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "probe", "name": "Probe", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

_counter = {"n": 0}


def _seed(db, *, lifecycle: str, backtest_trades: int = 0, paper_trades: int = 0, pnl: float = 0.0):
    _counter["n"] += 1
    n = _counter["n"]
    strategy = Strategy(name=f"Probe{n}", slug=f"probe-{n}", lifecycle=lifecycle)
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json=SAMPLE_DSL,
        immutable_hash="i" * 64,
        validation_status="valid",
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol=f"PROBE{n}", asset_class="stock")
    db.add(asset)
    db.flush()
    source = MarketDataSource(name=f"probe-src-{n}", base_url="x")
    db.add(source)
    db.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db.add(series)
    db.flush()
    if backtest_trades:
        run = BacktestRun(
            strategy_version_id=version.id,
            dataset_version_id=series.id,
            dataset_hash="d" * 64,
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(
            BacktestResult(
                backtest_run_id=run.id,
                summary_json={
                    "number_of_trades": backtest_trades,
                    "total_return": 0.12,
                    "max_drawdown": -0.1,
                    "sharpe": 1.2,
                },
                equity_curve_json=[],
                result_hash="r" * 64,
            )
        )
    if paper_trades:
        account = PaperAccount(
            name=f"probe-pa-{n}",
            strategy_id=strategy.id,
            initial_cash=10_000,
            cash=10_000 + pnl,
        )
        db.add(account)
        db.flush()
        for _ in range(paper_trades):
            db.add(
                PaperTrade(
                    account_id=account.id,
                    asset_id=asset.id,
                    direction="BUY",
                    entry_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                    entry_price=100.0,
                    exit_time=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                    exit_price=101.0,
                    quantity=1.0,
                    pnl=pnl / paper_trades,
                    strategy_version=f"{strategy.id}@{version.version}",
                )
            )
    db.commit()
    return strategy


def _last_action(db) -> str:
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "strategy_lifecycle_changed")
        .order_by(AuditLog.id.desc())
        .first()
    )
    return row.action if row else "<no audit event>"


def _check_directions() -> list[str]:
    failures: list[str] = []
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    cases = [
        ("a person retires a paper-traded strategy", "paper_trading", "retired", "retire"),
        ("a person retires a flagged strategy", "degraded", "retired", "retire"),
        ("the rule flags a losing paper record", "oos_tested", "degraded", "degrade"),
        ("the evidence promotes one step", "validated", "backtested", "promote"),
        ("a retired strategy comes back as a signal", "retired", "reference_signal", "restore"),
    ]
    try:
        print(f"vocabulary: {', '.join(LIFECYCLE_ACTIONS)}")
        for label, start, target, expect in cases:
            needs_paper = target in {"retired", "reference_signal", "degraded"} and start != "degraded"
            strategy = _seed(
                db,
                lifecycle=start,
                backtest_trades=25 if start == "validated" else 0,
                paper_trades=12 if needs_paper else 0,
                pnl=-500.0 if target == "degraded" else 400.0,
            )
            if target == "reference_signal":
                eligible = evaluate_lifecycle(db, strategy)["reference_eligible"]
                print(f"{'  reference gate open':42s} {eligible}")
                if not eligible:
                    failures.append(f"{label}: the reference gate closed unexpectedly")
            apply_lifecycle(db, strategy, target, actor="system" if target == "degraded" else "user")
            action = _last_action(db)
            print(f"{label:42s} {start:>14s} -> {target:<16s} action={action!r}")
            if action != expect:
                failures.append(f"{label}: recorded {action!r}, expected {expect!r}")
            if action not in LIFECYCLE_ACTIONS:
                failures.append(f"{label}: {action!r} is outside the vocabulary")
    finally:
        db.close()
        engine.dispose()
    return failures


def main() -> int:
    failures = _check_directions()
    if failures:
        print("\nRESULT: failures")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: every lifecycle move is recorded as the move it was")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
