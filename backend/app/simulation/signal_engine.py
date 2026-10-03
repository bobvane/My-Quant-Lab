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
import math
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.data.market_data_repo import load_bars
from app.data.strategy_service import load_spec
from app.data.symbols import canonical_symbol
from app.domain.models import (
    Asset,
    FeatureSnapshot,
    MarketDataSeries,
    Signal,
    StrategyVersion,
)
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


def load_portfolio_holdings() -> list[dict[str, Any]] | None:
    """Best-effort read-only Ghostfolio holdings; ``None`` when not configured.

    Never raises and never blocks a scan: a missing/broken Ghostfolio must leave
    signal generation unaffected (docs/09 §3, docs/14).
    """

    if not settings.ghostfolio_base_url or not settings.ghostfolio_api_key:
        return None
    try:
        from app.data.ghostfolio import GhostfolioAdapter

        return GhostfolioAdapter().get_portfolio_summary().get("holdings", [])
    except Exception:  # noqa: BLE001 - advisory context only
        logger.warning("Ghostfolio portfolio context unavailable", exc_info=True)
        return None


def portfolio_context_for(
    symbol: str | None, holdings: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Portfolio context for one symbol (docs/09 §3): quantity / value / weight."""

    if holdings is None:
        return {
            "ghostfolio_connected": False,
            "note": "Ghostfolio 未配置或不可达，信号不受影响。",
        }
    if not symbol:
        return {"ghostfolio_connected": True, "holding": None, "note": "未知标的。"}
    key = canonical_symbol(symbol)
    match = next(
        (
            h
            for h in holdings
            if canonical_symbol(str(h.get("symbol"))) == key
            or canonical_symbol(str(h.get("name"))) == key
        ),
        None,
    )
    if match is None:
        return {
            "ghostfolio_connected": True,
            "holding": None,
            "note": f"Ghostfolio 已连接，但未持有 {symbol}。",
        }
    weight = match.get("allocation_pct")
    held = f"{match.get('quantity')} 股" if match.get("quantity") is not None else "已持有"
    weight_text = f"，占比 {weight:.2f}%" if isinstance(weight, (int, float)) else ""
    return {
        "ghostfolio_connected": True,
        "holding": match,
        "note": f"你在 Ghostfolio 中持有 {symbol} {held}{weight_text}。",
    }


def _feature_values(frame: pd.DataFrame, ts: Any) -> dict[str, float | None]:
    """JSON-safe snapshot of the feature row the signal was computed from."""

    if ts not in frame.index:
        return {}
    values: dict[str, float | None] = {}
    for key, raw in frame.loc[ts].items():
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        values[str(key)] = None if math.isnan(number) else number
    return values


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
    feature_frame = build_features(bars, spec=spec)
    decisions, intent = run_strategy(spec, feature_frame.frame)
    last_ts = feature_frame.frame.index[-1]
    input_hash = _feature_hash(feature_frame.frame.tail(1))

    # ``event_bar_time`` is the bar the intent refers to; ``bar_time`` is the
    # latest closed bar this scan was run against. The executor now evaluates
    # only the latest bar, so a non-NO_SIGNAL state is always fresh. This is what
    # stops an old match from being re-persisted and re-notified every bar
    # (docs/09 §4: one event, one signal).
    state = intent.get("state", "NO_SIGNAL")
    direction = intent.get("direction", "FLAT")
    closes_direction = intent.get("closes_direction")
    triggered = intent.get("triggered_rules", [])
    event_bar_time = intent.get("bar_time")
    is_fresh = state != "NO_SIGNAL" and event_bar_time is not None

    if state == "NO_SIGNAL":
        reason = f"no rule matched on the last closed bar ({last_ts.isoformat()})"
    elif state == "WAIT":
        reason = (
            f"entry conditions partially met on the last closed bar ({last_ts.isoformat()}); "
            "waiting for the remaining conditions"
        )
    elif closes_direction is not None:
        reason = (
            f"the exit rule for a {closes_direction} position first held on the last closed bar "
            f"({last_ts.isoformat()}); this closes {closes_direction}, it does not open anything"
        )
    else:
        reason = f"rules matched on the last closed bar ({last_ts.isoformat()})"

    return ScanResult(
        state=state,
        direction=direction,
        closes_direction=closes_direction,
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
        feature_values=_feature_values(feature_frame.frame, last_ts),
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
    *,
    holdings: list[dict[str, Any]] | None = None,
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

    asset = db.get(Asset, series.asset_id)
    context = portfolio_context_for(asset.symbol if asset else None, holdings)
    signal = Signal(
        strategy_version_id=strategy_version.id,
        asset_id=series.asset_id,
        timeframe=series.timeframe,
        bar_timestamp=bar_time,
        state=intent["state"],
        direction=intent["direction"],
        closes_direction=intent.get("closes_direction"),
        price_reference=intent.get("price_reference"),
        stop_reference=intent.get("stop_reference"),
        target_reference=intent.get("target_reference"),
        triggered_rules_json=intent.get("triggered_rules", []),
        feature_snapshot_hash=intent["feature_snapshot_hash"],
        data_source=f"series:{series.id}/{series.dataset_version}",
        portfolio_context_json=context,
        status="new",
    )
    db.add(signal)

    _persist_feature_snapshot(db, series, bar_time, intent)

    db.commit()
    db.refresh(signal)
    return signal, True


def _persist_feature_snapshot(
    db: Session, series: MarketDataSeries, bar_time: Any, intent: ScanResult
) -> None:
    """Store the feature row behind a signal as reproducible evidence (docs/09 §5).

    Keyed uniquely by (series, bar, feature_version); re-running does not create
    duplicates. ``input_hash`` matches ``Signal.feature_snapshot_hash``.
    """

    values = intent.get("feature_values")
    feature_version = intent.get("feature_version") or FEATURE_VERSION
    if not values:
        return
    existing = db.scalar(
        select(FeatureSnapshot).where(
            FeatureSnapshot.series_id == series.id,
            FeatureSnapshot.bar_timestamp == bar_time,
            FeatureSnapshot.feature_version == feature_version,
        )
    )
    if existing is not None:
        return
    db.add(
        FeatureSnapshot(
            series_id=series.id,
            bar_timestamp=bar_time,
            feature_version=feature_version,
            values_json=values,
            input_hash=intent.get("feature_snapshot_hash", ""),
            available_at=bar_time,
        )
    )


def scan_series(
    db: Session, strategy_version: StrategyVersion, series: MarketDataSeries
) -> Signal | None:
    """Evaluate and persist a signal, respecting the de-duplication rule.

    Returns ``None`` when the latest closed bar produced nothing to record. A
    NO_SIGNAL row is not a signal: persisting one wrote a row per series per
    strategy version on every manual scan, which then showed up in the signal
    list, in the outcome counts and in the evaluator's pending queue (ADR-115).
    """

    intent = latest_intent_for_series(db, strategy_version, series)
    if not intent.get("is_fresh"):
        return None
    signal, _created = persist_signal(
        db, strategy_version, series, intent, holdings=load_portfolio_holdings()
    )
    return signal


def _scan_row(
    version: StrategyVersion,
    series: MarketDataSeries,
    asset: Asset | None,
    intent: ScanResult,
    *,
    persisted: bool | None = None,
) -> dict[str, Any]:
    """One row of a scan report. The same shape for dry runs and real runs."""

    row: dict[str, Any] = {
        "strategy_version_id": version.id,
        "strategy_id": version.strategy_id,
        "symbol": asset.symbol if asset else series.asset_id,
        "timeframe": series.timeframe,
        "state": intent["state"],
        "direction": intent["direction"],
        "closes_direction": intent.get("closes_direction"),
        "reason": intent["reason"],
        "bar_time": intent["bar_time"],
        "price_reference": intent.get("price_reference"),
        "stop_reference": intent.get("stop_reference"),
        "target_reference": intent.get("target_reference"),
        "triggered_rules": intent.get("triggered_rules", []),
        "scanned_at": dt.datetime.now(tz=dt.UTC).isoformat(),
    }
    if persisted is not None:
        row["persisted"] = persisted
    return row


def scan_all(db: Session, *, asset_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Scan every current strategy version against every series (dry run)."""

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
            results.append(_scan_row(version, series, asset, intent))
    return results


def scan_and_persist(db: Session, *, asset_ids: list[int] | None = None) -> dict[str, Any]:
    """Evaluate and persist every current strategy against every series.

    This is the scheduled-scanner path (docs/09 §2): evaluate on closed bars,
    persist de-duplicated signals, and report how many were newly created so the
    caller can notify on exactly the new ones. The per-row report is returned too,
    so a manual scan and a scheduled scan describe the same evaluation.
    """

    created = 0
    evaluated = 0
    results: list[dict[str, Any]] = []
    holdings = load_portfolio_holdings()  # fetched once per scan, not per signal
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
                asset = db.get(Asset, series.asset_id)
                results.append(_scan_row(version, series, asset, intent, persisted=False))
                continue
            try:
                _signal, was_created = persist_signal(
                    db, version, series, intent, holdings=holdings
                )
            except Exception:  # pragma: no cover - defensive
                db.rollback()
                logger.exception("persist failed for version=%s series=%s", version.id, series.id)
                continue
            if was_created:
                created += 1
            asset = db.get(Asset, series.asset_id)
            results.append(_scan_row(version, series, asset, intent, persisted=True))
    return {"evaluated": evaluated, "created": created, "signals": results}
