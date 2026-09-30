"""Signal endpoints.

**No broker endpoint exists in this project.** Signals are information only and
every user must decide independently whether to act.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import SignalOut
from app.core.db import get_db
from app.data.market_data_repo import load_bars
from app.data.strategy_service import load_spec
from app.domain.models import Asset, MarketDataSeries, Signal, StrategyVersion
from app.features.engine import build_features
from app.simulation.signal_engine import latest_intent_for_series, scan_all, scan_series
from app.strategies.executor import run_strategy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=list[SignalOut], summary="List persisted signals")
def list_signals(
    db: Session = Depends(get_db),
    state: str | None = None,
    asset_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[SignalOut]:
    stmt = select(Signal)
    if state:
        stmt = stmt.where(Signal.state == state)
    if asset_id:
        stmt = stmt.where(Signal.asset_id == asset_id)
    rows = db.scalars(stmt.order_by(Signal.id.desc()).limit(limit)).all()
    return [
        SignalOut(
            id=row.id,
            strategy_version_id=row.strategy_version_id,
            asset_id=row.asset_id,
            timeframe=row.timeframe,
            bar_timestamp=row.bar_timestamp,
            state=row.state,
            direction=row.direction,
            price_reference=float(row.price_reference) if row.price_reference else None,
            stop_reference=float(row.stop_reference) if row.stop_reference else None,
            target_reference=float(row.target_reference) if row.target_reference else None,
            triggered_rules=row.triggered_rules_json or [],
            status=row.status,
            generated_at=row.generated_at,
            explanation=row.explanation_json,
        )
        for row in rows
    ]


@router.get("/outcomes", summary="Signal outcome tracking (did signals work?)")
def list_outcomes(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    from app.domain.models import SignalOutcome

    rows = db.scalars(
        select(SignalOutcome, Signal)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .order_by(SignalOutcome.evaluated_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "signal_id": outcome.signal_id,
            "outcome_state": outcome.outcome_state,
            "direction": signal.direction,
            "timeframe": signal.timeframe,
            "bar_timestamp": signal.bar_timestamp,
            "entry_price": float(outcome.entry_price) if outcome.entry_price else None,
            "exit_price": float(outcome.exit_price) if outcome.exit_price else None,
            "pnl_pct": float(outcome.pnl_pct) if outcome.pnl_pct is not None else None,
            "mae_pct": float(outcome.mae) if outcome.mae is not None else None,
            "mfe_pct": float(outcome.mfe) if outcome.mfe is not None else None,
            "evaluated_at": outcome.evaluated_at,
            "notes": outcome.notes,
        }
        for outcome, signal in rows
    ]


@router.get("/{signal_id}", response_model=SignalOut, summary="Get one signal")
def get_signal(signal_id: int, db: Session = Depends(get_db)) -> SignalOut:
    row = db.get(Signal, signal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return SignalOut(
        id=row.id,
        strategy_version_id=row.strategy_version_id,
        asset_id=row.asset_id,
        timeframe=row.timeframe,
        bar_timestamp=row.bar_timestamp,
        state=row.state,
        direction=row.direction,
        price_reference=float(row.price_reference) if row.price_reference else None,
        stop_reference=float(row.stop_reference) if row.stop_reference else None,
        target_reference=float(row.target_reference) if row.target_reference else None,
        triggered_rules=row.triggered_rules_json or [],
        status=row.status,
        generated_at=row.generated_at,
        explanation=row.explanation_json,
    )


@router.post("/{signal_id}/acknowledge", summary="Acknowledge a signal")
def acknowledge(signal_id: int, db: Session = Depends(get_db)) -> dict:
    import datetime as dt

    row = db.get(Signal, signal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="signal not found")
    row.status = "acknowledged"
    row.acknowledged_at = dt.datetime.now(tz=dt.UTC)
    db.commit()
    return {"id": signal_id, "status": row.status}


@router.post("/scan", summary="Evaluate every current strategy on every series")
def scan(db: Session = Depends(get_db), persist: bool = False) -> dict[str, Any]:
    """Dry-run a scan. With ``persist=true`` signals are stored (deduplicated)."""

    results = scan_all(db)
    created = 0
    if persist:
        for row in results:
            version = db.get(StrategyVersion, row["strategy_version_id"])
            series = db.get(MarketDataSeries, _series_id_for(db, row["symbol"], row["timeframe"]))
            if version is None or series is None:
                continue
            before = db.scalar(select(Signal).where(Signal.strategy_version_id == version.id))
            scan_series(db, version, series)
            after = db.scalar(
                select(Signal).where(
                    Signal.strategy_version_id == version.id,
                    Signal.asset_id == series.asset_id,
                    Signal.bar_timestamp == row["bar_time"],
                )
            )
            if after is not None and (before is None or before.id != after.id):
                created += 1
    return {
        "evaluated": len(results),
        "created": created,
        "signals": results,
        "disclaimer": "Signals are research information only. No order is ever placed.",
    }


@router.get("/preview/{strategy_version_id}", summary="Preview signal for one strategy")
def preview(
    strategy_version_id: int,
    db: Session = Depends(get_db),
    symbol: str | None = None,
    timeframe: str = "1d",
) -> dict[str, Any]:
    version = db.get(StrategyVersion, strategy_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    asset = None
    if symbol:
        asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
        if asset is None:
            raise HTTPException(status_code=404, detail=f"asset '{symbol}' not found")
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.timeframe == timeframe,
            *([MarketDataSeries.asset_id == asset.id] if asset else []),
        )
    )
    if series is None:
        raise HTTPException(status_code=404, detail="no matching market data series")
    return dict(latest_intent_for_series(db, version, series))


@router.get("/evidence/{strategy_version_id}", summary="Five-layer evidence for a signal")
def evidence(
    strategy_version_id: int,
    db: Session = Depends(get_db),
    symbol: str | None = None,
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Rule match + empirical stats + paper stats + portfolio context.

    The AI explanation would be the fifth layer; it is only ever *added* to these
    deterministic layers, never substituted for them.
    """

    version = db.get(StrategyVersion, strategy_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol)) if symbol else None
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.timeframe == timeframe,
            *([MarketDataSeries.asset_id == asset.id] if asset else []),
        )
    )
    if series is None:
        raise HTTPException(status_code=404, detail="no matching market data series")

    bars = load_bars(db, series, only_closed=True, limit=800)
    feature_frame = build_features(bars)
    spec = load_spec(version)
    decisions, intent = run_strategy(spec, feature_frame.frame)

    last_ts = feature_frame.frame.index[-1]
    layer_1 = {
        "bar_time": last_ts.isoformat(),
        "entry_long": bool(decisions["entry_long"].iloc[-1]),
        "exit_long": bool(decisions["exit_long"].iloc[-1]),
        "close": float(feature_frame.frame["close"].iloc[-1]),
        "atr14": float(feature_frame.frame["atr14"].iloc[-1])
        if not pd_isna(feature_frame.frame["atr14"].iloc[-1])
        else None,
    }
    from app.domain.models import BacktestRun, PaperTrade

    runs = db.scalars(
        select(BacktestRun)
        .where(BacktestRun.strategy_version_id == version.id, BacktestRun.status == "completed")
        .order_by(BacktestRun.id.desc())
        .limit(5)
    ).all()
    layer_2 = [
        {
            "backtest_run_id": run.id,
            "created_at": run.created_at.isoformat(),
            **(run.result.summary_json if run.result else {}),
        }
        for run in runs
    ]
    paper = db.scalars(
        select(PaperTrade).where(
            PaperTrade.strategy_version == f"{version.strategy_id}@{version.version}"
        )
    ).all()
    layer_3 = {
        "paper_trades": len(paper),
        "realized_pnl": sum(float(t.pnl or 0) for t in paper if t.exit_time is not None),
    }

    # Layer 4: Ghostfolio portfolio context (real holdings)
    layer_4: dict[str, Any] = {
        "ghostfolio_connected": False,
        "holdings_for_symbol": None,
        "note": "Ghostfolio is not configured or unreachable.",
    }
    symbol_name = asset.symbol if asset else None
    if symbol_name:
        try:
            from app.data.ghostfolio import GhostfolioAdapter

            adapter = GhostfolioAdapter()
            portfolio = adapter.get_portfolio_summary()
            layer_4["ghostfolio_connected"] = True
            layer_4["total_value"] = portfolio.get("total_value")
            layer_4["holdings_count"] = portfolio.get("holdings_count")
            matching = next(
                (
                    h
                    for h in portfolio.get("holdings", [])
                    if h.get("symbol", "").upper() == symbol_name.upper()
                ),
                None,
            )
            if matching:
                layer_4["holdings_for_symbol"] = matching
                layer_4["note"] = (
                    f"你在 Ghostfolio 中持有 {matching['symbol']} "
                    f"{matching['quantity']} 股（占比 {matching.get('allocation_pct', 0):.1f}%）"
                )
            else:
                layer_4["note"] = f"Ghostfolio 已连接，但未持有 {symbol_name}。"
        except Exception:
            logger.debug("Ghostfolio portfolio context unavailable", exc_info=True)
            layer_4["note"] = "Ghostfolio 连接失败，信号不受影响。"

    return {
        "strategy_version_id": version.id,
        "symbol": asset.symbol if asset else None,
        "timeframe": timeframe,
        "layer_1_rule_match": layer_1,
        "layer_2_empirical_stats": layer_2,
        "layer_3_paper_stats": layer_3,
        "layer_4_portfolio_context": layer_4,
        "signal_intent": intent,
    }


def pd_isna(value: Any) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(value))
    except Exception:  # pragma: no cover
        return True


def _series_id_for(db: Session, symbol: str, timeframe: str) -> int | None:
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        return None
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.asset_id == asset.id, MarketDataSeries.timeframe == timeframe
        )
    )
    return series.id if series else None
