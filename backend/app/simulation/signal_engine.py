"""Signal engine.

Signals are produced by the deterministic strategy executor on **closed bars**
only, deduplicated by (strategy version, asset, timeframe, bar timestamp) and
stored together with the evidence needed to reproduce them.

The AI layer may add an explanation afterwards. It can never change the state.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.market_data_repo import load_bars
from app.data.strategy_service import load_spec
from app.domain.models import Asset, MarketDataSeries, Signal, StrategyVersion
from app.features.engine import FEATURE_VERSION, build_features
from app.strategies.executor import run_strategy

logger = logging.getLogger(__name__)

__all__ = [
    "ScanResult",
    "latest_intent_for_series",
    "persist_signal",
    "scan_all",
    "scan_and_persist",
    "scan_series",
]


class ScanResult(dict):
    """Simple dict subclass so the service stays trivially testable."""


def _feature_hash(bars: pd.DataFrame) -> str:
    hasher = hashlib.sha256()
    hasher.update(bars.to_csv(float_format="%.10g").encode("utf-8"))
    return hasher.hexdigest()


def latest_intent_for_series(
    db: Session, strategy_version: StrategyVersion, series: MarketDataSeries
) -> ScanResult:
    """Evaluate the newest **closed** bar and return a structured intent."""

    bars = load_bars(db, series, only_closed=True, limit=800)
    if len(bars) < 60:
        return ScanResult(
            state="NO_SIGNAL",
            direction="FLAT",
            reason=f"insufficient closed bars ({len(bars)})",
            bar_time=None,
            strategy_version_id=strategy_version.id,
        )

    spec = load_spec(strategy_version)
    feature_frame = build_features(bars)
    decisions, intent = run_strategy(spec, feature_frame.frame)
    last_ts = feature_frame.frame.index[-1]
    input_hash = _feature_hash(feature_frame.frame.tail(1))

    # ``event_bar_time`` is the bar the rules actually fired on; ``bar_time`` is
    # the latest closed bar this scan was run against. ``is_fresh`` means the
    # event really happened on that latest bar (docs/09 §4: one event, one
    # signal — an old match must not be re-persisted and re-notified each bar).
    event_bar_time = intent.get("bar_time")
    is_fresh = event_bar_time is not None and event_bar_time == last_ts

    if intent.get("bar_time") is None:
        state, direction, triggered = "NO_SIGNAL", "FLAT", []
        reason = f"no rule matched on the last closed bar ({last_ts.isoformat()})"
    else:
        state = intent["state"]
        direction = intent["direction"]
        triggered = intent.get("triggered_rules", [])
        reason = f"rules matched on the last closed bar ({last_ts.isoformat()})"

    return ScanResult(
        state=state,
        direction=direction,
        reason=reason,
        bar_time=last_ts,
        event_bar_time=event_bar_time,
        is_fresh=is_fresh,
        triggered_rules=triggered,
        price_reference=intent.get("price_reference"),
        stop_reference=intent.get("stop_reference"),
        target_reference=intent.get("target_reference"),
        feature_snapshot_hash=input_hash,
        feature_version=FEATURE_VERSION,
        strategy_version_id=strategy_version.id,
        series_id=series.id,
        asset_id=series.asset_id,
        timeframe=series.timeframe,
        dataset_version=series.dataset_version,
    )


def persist_signal(
    db: Session,
    strategy_version: StrategyVersion,
    series: MarketDataSeries,
    intent: ScanResult,
) -> tuple[Signal, bool]:
    """Persist one evaluated intent, respecting the de-duplication rule.

    Returns ``(signal, created)`` where ``created`` is ``False`` when a signal
    for the same (strategy version, asset, timeframe, bar) already exists.
    """

    bar_time = intent.get("bar_time")
    if bar_time is None:
        raise ValueError("no closed bar available for this series")

    existing = db.scalar(
        select(Signal).where(
            Signal.strategy_version_id == strategy_version.id,
            Signal.asset_id == series.asset_id,
            Signal.timeframe == series.timeframe,
            Signal.bar_timestamp == bar_time,
        )
    )
    if existing is not None:
        logger.info(
            "signal already exists for strategy=%s asset=%s bar=%s (no duplicate created)",
            strategy_version.id,
            series.asset_id,
            bar_time,
        )
        return existing, False

    signal = Signal(
        strategy_version_id=strategy_version.id,
        asset_id=series.asset_id,
        timeframe=series.timeframe,
        bar_timestamp=bar_time,
        state=intent["state"],
        direction=intent["direction"],
        price_reference=intent.get("price_reference"),
        stop_reference=intent.get("stop_reference"),
        target_reference=intent.get("target_reference"),
        triggered_rules_json=intent.get("triggered_rules", []),
        feature_snapshot_hash=intent["feature_snapshot_hash"],
        data_source=f"series:{series.id}/{series.dataset_version}",
        status="new",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal, True


def scan_series(db: Session, strategy_version: StrategyVersion, series: MarketDataSeries) -> Signal:
    """Evaluate and persist a signal, respecting the de-duplication rule."""

    intent = latest_intent_for_series(db, strategy_version, series)
    signal, _created = persist_signal(db, strategy_version, series, intent)
    return signal


def scan_all(db: Session, *, asset_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Scan every current strategy version against every series."""

    results: list[dict[str, Any]] = []
    versions = db.scalars(select(StrategyVersion).where(StrategyVersion.is_current.is_(True))).all()
    series_stmt = select(MarketDataSeries).where(MarketDataSeries.is_archived.is_(False))
    if asset_ids:
        series_stmt = series_stmt.where(MarketDataSeries.asset_id.in_(asset_ids))
    series_rows = db.scalars(series_stmt).all()

    for version in versions:
        for series in series_rows:
            try:
                intent = latest_intent_for_series(db, version, series)
            except Exception:  # pragma: no cover - defensive
                logger.exception("scan failed for version=%s series=%s", version.id, series.id)
                continue
            asset = db.get(Asset, series.asset_id)
            results.append(
                {
                    "strategy_version_id": version.id,
                    "strategy_id": version.strategy_id,
                    "symbol": asset.symbol if asset else series.asset_id,
                    "timeframe": series.timeframe,
                    "state": intent["state"],
                    "direction": intent["direction"],
                    "reason": intent["reason"],
                    "bar_time": intent["bar_time"],
                    "price_reference": intent.get("price_reference"),
                    "stop_reference": intent.get("stop_reference"),
                    "target_reference": intent.get("target_reference"),
                    "triggered_rules": intent.get("triggered_rules", []),
                    "scanned_at": dt.datetime.now(tz=dt.UTC).isoformat(),
                }
            )
    return results


def scan_and_persist(db: Session, *, asset_ids: list[int] | None = None) -> dict[str, Any]:
    """Evaluate and persist every current strategy against every series.

    This is the scheduled-scanner path (docs/09 §2): evaluate on closed bars,
    persist de-duplicated signals, and report how many were newly created so the
    caller can notify on exactly the new ones.
    """

    created = 0
    evaluated = 0
    versions = db.scalars(select(StrategyVersion).where(StrategyVersion.is_current.is_(True))).all()
    series_stmt = select(MarketDataSeries).where(MarketDataSeries.is_archived.is_(False))
    if asset_ids:
        series_stmt = series_stmt.where(MarketDataSeries.asset_id.in_(asset_ids))
    series_rows = db.scalars(series_stmt).all()

    for version in versions:
        for series in series_rows:
            try:
                intent = latest_intent_for_series(db, version, series)
            except Exception:  # pragma: no cover - defensive
                db.rollback()
                logger.exception("scan failed for version=%s series=%s", version.id, series.id)
                continue
            evaluated += 1
            # Only persist a signal that genuinely fired on the latest closed
            # bar. NO_SIGNAL rows and stale historical matches are skipped so the
            # table does not grow one row per bar and nothing gets re-notified.
            if not intent.get("is_fresh"):
                continue
            try:
                _signal, was_created = persist_signal(db, version, series, intent)
            except Exception:  # pragma: no cover - defensive
                db.rollback()
                logger.exception("persist failed for version=%s series=%s", version.id, series.id)
                continue
            if was_created:
                created += 1
    return {"evaluated": evaluated, "created": created}
