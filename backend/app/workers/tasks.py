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
from app.simulation.signal_engine import scan_all
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
    """Evaluate all current strategies on all series (closed bars only)."""
    with session_scope() as db:
        results = scan_all(db)
    by_state: dict[str, int] = {}
    for row in results:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
    return {"evaluated": len(results), "by_state": by_state, "results": results[:50]}


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


@celery_app.task(name="quantlab.purge_resources")
def purge_resources() -> dict:
    """Enforce retention: raw 7d, rollups 30d (configurable)."""

    from app.infrastructure.resource_store import purge_expired

    with session_scope() as db:
        deleted = purge_expired(db)
    return {"deleted": deleted}
