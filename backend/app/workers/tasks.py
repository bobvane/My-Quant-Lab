"""Background tasks.

These jobs only ever compute or *record* information. Nothing here places an
order with a broker, and imported third-party code is never executed here.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app.core.config import settings
from app.core.db import session_scope
from app.data.market_data_repo import frame_to_bars, get_or_create_series, upsert_bars
from app.data.providers import asset_metadata_for, get_market_data_provider, mark_closed_bars
from app.domain.models import Asset, MarketDataSource
from app.notifications.service import notify_pending_signals
from app.simulation.outcome_evaluator import evaluate_pending_outcomes
from app.simulation.signal_engine import scan_and_persist
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def resolve_watchlist(provider: Any, requested: list[str] | None = None) -> list[str]:
    """Decide which symbols the scheduled sync should keep warm.

    Priority: explicit argument → ``MARKET_DATA_WATCHLIST`` setting → whatever
    the active provider declares. The old code hard-coded ``DEMO-*`` tickers,
    which silently broke as soon as a real provider was configured.
    """

    if requested:
        return list(requested)
    if settings.market_data_watchlist:
        return list(settings.market_data_watchlist)
    return [
        str(entry["symbol"])
        for entry in provider.list_assets()
        if isinstance(entry, dict) and entry.get("symbol")
    ]


@celery_app.task(name="quantlab.sync_market_data")
def sync_market_data(symbols: list[str] | None = None) -> dict:
    """Fetch OHLCV for the watchlist and upsert it idempotently."""

    provider = get_market_data_provider()
    targets = resolve_watchlist(provider, symbols)
    end = dt.datetime.now(tz=dt.UTC)
    start = end - dt.timedelta(days=400)
    summary: dict[str, int] = {}

    with session_scope() as db:
        source = db.query(MarketDataSource).filter_by(name=provider.name).one_or_none()
        if source is None:
            source = MarketDataSource(
                name=provider.name, provider_type="rest_api", base_url=f"provider://{provider.name}"
            )
            db.add(source)
            db.flush()

        for symbol in targets:
            try:
                frame = provider.get_ohlcv(symbol, "1d", start, end)
            except Exception:
                logger.exception("sync failed for %s", symbol)
                summary[symbol] = -1
                continue
            if frame.empty:
                logger.warning(
                    "provider returned no bars for %s (rate limit or bad ticker)", symbol
                )
                summary[symbol] = 0
                continue
            # A daily candle covering today is still forming; store it as
            # not-closed so strategies never read an unfinished bar.
            frame = mark_closed_bars(frame, "1d", settings.default_timezone)
            asset = db.query(Asset).filter_by(symbol=symbol).one_or_none()
            if asset is None:
                meta = asset_metadata_for(provider, symbol)
                asset = Asset(
                    symbol=symbol,
                    display_name=meta.get("display_name") or symbol,
                    asset_class=str(meta.get("asset_class") or "stock"),
                    currency=str(meta.get("currency") or "USD"),
                    exchange=meta.get("exchange"),
                )
                db.add(asset)
                db.flush()
            series = get_or_create_series(db, asset=asset, timeframe="1d", source_id=source.id)
            summary[symbol] = upsert_bars(db, series, frame_to_bars(frame))
            series.quality_status = "valid"
    return {"provider": provider.name, "watchlist": targets, "inserted": summary}


@celery_app.task(name="quantlab.scan_signals")
def scan_signals() -> dict:
    """Evaluate all current strategies on all series (closed bars only).

    Persists de-duplicated signals and then notifies on the pending ones, which
    is the documented pipeline: evaluate → persist → notify (docs/09 §2).
    """
    with session_scope() as db:
        result = scan_and_persist(db)
        notification = notify_pending_signals(db)
    return {**result, "notification": notification}


@celery_app.task(name="quantlab.notify_signals")
def notify_signals() -> dict:
    """Notify on any eligible signal that has not been sent yet.

    Separate from the scan so signals persisted through the API are still
    delivered, and so a failing webhook can never block the scanner.
    """
    with session_scope() as db:
        return notify_pending_signals(db)


@celery_app.task(name="quantlab.collect_resources")
def collect_resources() -> dict:
    """Sample NAS + container resources. Intentionally cheap: one cycle is a
    handful of /proc reads plus a couple of small inserts."""

    if not settings.resource_collection_enabled:
        return {"skipped": "disabled"}
    from app.infrastructure.resource_monitor import collect_cycle
    from app.infrastructure.resource_store import roll_up_recent, save_cycle

    with session_scope() as db:
        cycle = collect_cycle()
        rows = save_cycle(db, cycle)
        roll_up_recent(db)
    quantlab = cycle["quantlab"]
    return {
        "containers": len(cycle["containers"]),
        "rows": rows,
        "quantlab_cpu": quantlab["cpu"],
        "quantlab_mem_mb": quantlab["mem_mb"],
    }


@celery_app.task(name="quantlab.evaluate_strategy_lifecycle")
def evaluate_strategy_lifecycle() -> dict:
    """Promote/degrade strategies using the deterministic evidence rules.

    Never applies manual-only stages; every change is audit-logged with its
    evidence snapshot (docs/15 Phase 8).
    """

    if not settings.lifecycle_auto_enabled:
        return {"skipped": "lifecycle auto-evaluation disabled"}
    from sqlalchemy import select

    from app.domain.models import Strategy
    from app.strategies.lifecycle import LifecycleError, apply_lifecycle, evaluate_lifecycle

    applied: list[dict] = []
    with session_scope() as db:
        for strategy in db.scalars(select(Strategy).order_by(Strategy.id)).all():
            evaluation = evaluate_lifecycle(db, strategy)
            target = evaluation["suggested_next"]
            if not target or target in evaluation["manual_only_stages"]:
                continue
            try:
                apply_lifecycle(db, strategy, target, actor="system", evaluation=evaluation)
            except LifecycleError:
                logger.warning("lifecycle transition rejected for %s", strategy.id)
                continue
            applied.append(
                {"strategy_id": strategy.id, "from": evaluation["current"], "to": target}
            )
    return {"count": len(applied), "applied": applied}


@celery_app.task(name="quantlab.evaluate_signal_outcomes")
def evaluate_signal_outcomes() -> dict:
    """Look forward in price data for signals without outcomes and record
    pnl_pct / MAE / MFE. This is the "learn from results" mechanism."""

    with session_scope() as db:
        result = evaluate_pending_outcomes(db)
    return result


@celery_app.task(name="quantlab.purge_resources")
def purge_resources() -> dict:
    """Enforce retention: raw 7d, rollups 30d (configurable)."""

    from app.infrastructure.resource_store import purge_expired

    with session_scope() as db:
        deleted = purge_expired(db)
    return {"deleted": deleted}
