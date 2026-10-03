"""Strategy lifecycle tests (docs/15 Phase 8).

The contract under test: every promotion/degradation is derived from recorded
evidence with fixed thresholds, moves at most one stage at a time, is audited
with its evidence, and never lets a manual-only stage (reference_signal,
retired) happen by itself.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.data.strategy_service import record_audit
from app.domain.models import (
    Asset,
    BacktestResult,
    BacktestRun,
    MarketDataSeries,
    MarketDataSource,
    PaperAccount,
    PaperTrade,
    Strategy,
    StrategyVersion,
)
from app.strategies.lifecycle import (
    LIFECYCLE_ACTIONS,
    LifecycleError,
    LifecycleThresholds,
    apply_lifecycle,
    evaluate_lifecycle,
)

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "lc", "name": "LC", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 0, "slippage_bps": 0},
}


def _base(db, *, valid: bool = True, lifecycle: str = "imported"):
    strategy = Strategy(name="LC", slug=f"lc-{strategy_id_seed()}", lifecycle=lifecycle)
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json=_DSL,
        immutable_hash="i" * 64,
        validation_status="valid" if valid else "invalid",
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol=f"LC{asset_id_seed()}", asset_class="stock")
    db.add(asset)
    db.flush()
    source = MarketDataSource(name=f"lc-src-{source_id_seed()}", base_url="x")
    db.add(source)
    db.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db.add(series)
    db.flush()
    db.commit()
    return strategy, version, series, asset


_counter = {"n": 0}


def _next() -> int:
    _counter["n"] += 1
    return _counter["n"]


def strategy_id_seed() -> int:
    return _next()


asset_id_seed = strategy_id_seed
source_id_seed = strategy_id_seed


def _add_backtest(db, version, series, *, trades: int, total_return: float = 0.12) -> None:
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
                "number_of_trades": trades,
                "total_return": total_return,
                "max_drawdown": -0.1,
                "sharpe": 1.2,
            },
            equity_curve_json=[],
            result_hash="r" * 64,
        )
    )
    db.commit()


def _add_oos(db, version, *, windows: int = 3) -> None:
    record_audit(
        db,
        event_type="walk_forward_completed",
        entity_type="strategy_version",
        entity_id=str(version.id),
        action="run",
        payload={"windows": windows, "summary": {"mean_oos_return": 0.01}},
    )
    db.commit()


def _add_paper(db, strategy, version, asset, *, trades: int, pnl: float) -> None:
    account = PaperAccount(
        name=f"pa-{_next()}", strategy_id=strategy.id, initial_cash=10_000, cash=10_000 + pnl
    )
    db.add(account)
    db.flush()
    for _ in range(trades):
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
                pnl=(pnl / trades) if trades else 0.0,
                strategy_version=f"{strategy.id}@{version.version}",
            )
        )
    db.commit()


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def test_no_evidence_offers_only_normalization(db_session) -> None:
    strategy, _version, _series, _asset = _base(db_session, valid=False)
    evaluation = evaluate_lifecycle(db_session, strategy)
    assert evaluation["current"] == "imported"
    assert evaluation["suggested_next"] == "normalized"
    assert evaluation["reference_eligible"] is False


def test_promotes_one_step_at_a_time_with_evidence(db_session) -> None:
    strategy, version, series, asset = _base(db_session)

    evaluation = evaluate_lifecycle(db_session, strategy)
    assert evaluation["suggested_next"] == "normalized"
    apply_lifecycle(db_session, strategy, "normalized")
    assert strategy.lifecycle == "normalized"

    evaluation = evaluate_lifecycle(db_session, strategy)
    assert evaluation["suggested_next"] == "validated"
    apply_lifecycle(db_session, strategy, "validated")

    # Backtest with too few trades is not enough evidence.
    _add_backtest(db_session, version, series, trades=3)
    assert evaluate_lifecycle(db_session, strategy)["suggested_next"] is None
    assert "backtested" in evaluate_lifecycle(db_session, strategy)["blocked_reason"]

    _add_backtest(db_session, version, series, trades=25)
    assert evaluate_lifecycle(db_session, strategy)["suggested_next"] == "backtested"
    apply_lifecycle(db_session, strategy, "backtested")

    _add_oos(db_session, version, windows=3)
    assert evaluate_lifecycle(db_session, strategy)["suggested_next"] == "oos_tested"
    apply_lifecycle(db_session, strategy, "oos_tested")

    _add_paper(db_session, strategy, version, asset, trades=12, pnl=250.0)
    evaluation = evaluate_lifecycle(db_session, strategy)
    assert evaluation["suggested_next"] == "paper_trading"
    assert evaluation["reference_eligible"] is True
    apply_lifecycle(db_session, strategy, "paper_trading")
    assert strategy.lifecycle == "paper_trading"


def test_apply_is_refused_without_evidence(db_session) -> None:
    strategy, _version, _series, _asset = _base(db_session)
    with pytest.raises(LifecycleError):
        apply_lifecycle(db_session, strategy, "backtested")


def test_reference_signal_is_manual_and_needs_profitable_paper(db_session) -> None:
    strategy, version, series, asset = _base(db_session, lifecycle="paper_trading")
    _add_paper(db_session, strategy, version, asset, trades=12, pnl=-40.0)

    # A losing paper record must not be promotable to a reference signal.
    with pytest.raises(LifecycleError):
        apply_lifecycle(db_session, strategy, "reference_signal")

    _add_paper(db_session, strategy, version, asset, trades=12, pnl=400.0)
    evaluation = evaluate_lifecycle(db_session, strategy)
    assert evaluation["reference_eligible"] is True
    apply_lifecycle(db_session, strategy, "reference_signal")
    assert strategy.lifecycle == "reference_signal"


def test_degradation_rule_flags_losing_paper_strategy(db_session) -> None:
    strategy, version, series, asset = _base(db_session, lifecycle="oos_tested")
    _add_paper(db_session, strategy, version, asset, trades=15, pnl=-500.0)

    evaluation = evaluate_lifecycle(db_session, strategy)
    assert evaluation["degraded"] is True
    assert evaluation["suggested_next"] == "degraded"
    apply_lifecycle(db_session, strategy, "degraded")
    assert strategy.lifecycle == "degraded"


def test_lifecycle_change_is_audited_with_evidence(db_session) -> None:
    strategy, _version, _series, _asset = _base(db_session)
    apply_lifecycle(db_session, strategy, "normalized", actor="user", note="reviewed")

    events = _lifecycle_events(db_session)
    assert len(events) == 1
    payload = events[0].payload_json or {}
    assert payload["from"] == "imported"
    assert payload["to"] == "normalized"
    assert "evidence" in payload
    assert events[0].action in LIFECYCLE_ACTIONS


# --------------------------------------------------------------------------- #
# Direction in the audit trail (ADR-063)
# --------------------------------------------------------------------------- #
def _lifecycle_events(db_session) -> list:
    from app.domain.models import AuditLog

    return [
        row
        for row in db_session.query(AuditLog).all()
        if row.event_type == "strategy_lifecycle_changed"
    ]


def _last_lifecycle_event(db_session):
    events = _lifecycle_events(db_session)
    assert events, "the lifecycle change was not audited at all"
    return events[-1]


def test_retiring_a_strategy_is_recorded_as_a_retirement(db_session) -> None:
    strategy, version, _series, asset = _base(db_session, lifecycle="paper_trading")
    _add_paper(db_session, strategy, version, asset, trades=12, pnl=400.0)

    apply_lifecycle(db_session, strategy, "retired", actor="user", note="no longer used")

    event = _last_lifecycle_event(db_session)
    # ``retired`` sits at the end of MANUAL_ONLY, so comparing ranks made the
    # move out of the pipeline look like the largest promotion available.
    assert event.action == "retire"
    assert event.payload_json["from"] == "paper_trading"
    assert event.payload_json["to"] == "retired"


def test_retiring_a_degraded_strategy_is_not_a_promotion(db_session) -> None:
    strategy, _version, _series, _asset = _base(db_session, lifecycle="degraded")

    apply_lifecycle(db_session, strategy, "retired", actor="user")

    assert _last_lifecycle_event(db_session).action == "retire"


def test_a_step_forward_is_still_recorded_as_a_promotion(db_session) -> None:
    strategy, version, series, _asset = _base(db_session, lifecycle="validated")
    _add_backtest(db_session, version, series, trades=25)

    apply_lifecycle(db_session, strategy, "backtested", actor="user")

    event = _last_lifecycle_event(db_session)
    assert event.action == "promote"
    assert event.payload_json["to"] == "backtested"


def test_a_losing_paper_strategy_is_recorded_as_a_degradation(db_session) -> None:
    strategy, version, _series, asset = _base(db_session, lifecycle="oos_tested")
    _add_paper(db_session, strategy, version, asset, trades=15, pnl=-500.0)

    apply_lifecycle(db_session, strategy, "degraded", actor="system")

    event = _last_lifecycle_event(db_session)
    assert event.action == "degrade"
    assert event.payload_json["to"] == "degraded"


def test_leaving_a_terminal_stage_is_recorded_as_a_restore(db_session) -> None:
    strategy, version, _series, asset = _base(db_session, lifecycle="retired")
    _add_paper(db_session, strategy, version, asset, trades=12, pnl=400.0)
    assert evaluate_lifecycle(db_session, strategy)["reference_eligible"] is True

    apply_lifecycle(db_session, strategy, "reference_signal", actor="user")

    event = _last_lifecycle_event(db_session)
    assert event.action == "restore"
    assert event.payload_json["from"] == "retired"
    assert event.payload_json["to"] == "reference_signal"


def test_custom_thresholds_are_honoured(db_session) -> None:
    strategy, version, series, _asset = _base(db_session, lifecycle="validated")
    _add_backtest(db_session, version, series, trades=20)
    strict = LifecycleThresholds(min_backtest_trades=100)
    assert evaluate_lifecycle(db_session, strategy, thresholds=strict)["suggested_next"] is None
    assert evaluate_lifecycle(db_session, strategy)["suggested_next"] == "backtested"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_lifecycle_api_overview_and_apply(client) -> None:
    strategy = client.post("/api/v1/strategies", json={"name": "Lifecycle API"}).json()

    overview = client.get("/api/v1/lifecycle/strategies")
    assert overview.status_code == 200
    entry = next(row for row in overview.json() if row["strategy_id"] == strategy["id"])
    assert entry["current"] == "imported"

    # No version yet: promoting past normalization is refused.
    refused = client.post(
        f"/api/v1/lifecycle/strategies/{strategy['id']}/apply",
        json={"target_stage": "backtested"},
    )
    assert refused.status_code == 422

    # A valid version unlocks normalization only.
    client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": _DSL},
    )
    applied = client.post(
        f"/api/v1/lifecycle/strategies/{strategy['id']}/apply",
        json={"target_stage": "normalized"},
    )
    assert applied.status_code == 200
    assert applied.json()["current"] == "normalized"

    detail = client.get(f"/api/v1/lifecycle/strategies/{strategy['id']}").json()
    assert detail["current"] == "normalized"
    assert any(stage["stage"] == "paper_trading" for stage in detail["stages"])


def test_lifecycle_api_unknown_strategy_404(client) -> None:
    assert client.get("/api/v1/lifecycle/strategies/9999").status_code == 404
