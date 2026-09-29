"""Background tasks.

These jobs only ever compute or *record* information. Nothing here places an
order with a broker, and imported third-party code is never executed here.
"""

from __future__ import annotations

import datetime as dt
import logging

from app.core.db import session_scope
from app.data.market_data_repo import frame_to_bars, get_or_create_series, upsert_bars
from app.data.providers import get_market_data_provider
from app.domain.models import Asset, MarketDataSource
from app.simulation.signal_engine import scan_all
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Assets the scheduler keeps warm. Configurable via settings in production.
DEFAULT_WATCHLIST = ["DEMO-AAPL", "DEMO-BTC"]


@celery_app.task(name="quantlab.sync_market_data")
def sync_market_data(symbols: list[str] | None = None) -> dict:
    """Fetch OHLCV for the watchlist and upsert it idempotently."""

    targets = symbols or DEFAULT_WATCHLIST
    provider = get_market_data_provider()
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
                summary[symbol] = 0
                continue
            asset = db.query(Asset).filter_by(symbol=symbol).one_or_none()
            if asset is None:
                asset = Asset(symbol=symbol, display_name=symbol, asset_class="stock")
                db.add(asset)
                db.flush()
            series = get_or_create_series(db, asset=asset, timeframe="1d", source_id=source.id)
            summary[symbol] = upsert_bars(db, series, frame_to_bars(frame))
            series.quality_status = "valid"
    return {"inserted": summary}


@celery_app.task(name="quantlab.scan_signals")
def scan_signals() -> dict:
    """Evaluate all current strategies on all series (closed bars only)."""

    with session_scope() as db:
        results = scan_all(db)
    by_state: dict[str, int] = {}
    for row in results:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
    return {"evaluated": len(results), "by_state": by_state, "results": results[:50]}
