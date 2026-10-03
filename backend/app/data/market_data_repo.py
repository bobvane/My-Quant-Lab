"""Market data repository helpers (DB <-> DataFrame).

Bars are loaded as a UTC-indexed DataFrame; this is the only representation the
feature engine and the backtest engine accept.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Asset, MarketDataBar, MarketDataSeries

__all__ = [
    "assess_bars_quality",
    "bars_to_frame",
    "frame_to_bars",
    "get_or_create_series",
    "load_bars",
    "latest_closed_bar_time",
    "refresh_series_content_hash",
    "series_content_hash",
    "upsert_bars",
]

BAR_COLUMNS = ["open", "high", "low", "close", "volume", "amount", "is_closed", "source_hash"]

# Daily bars with a bigger jump than this are treated as a data gap. Weekends
# (2 days) and most holidays (<=4 days) stay below it.
GAP_THRESHOLD_DAYS = 5


def assess_bars_quality(
    frame: pd.DataFrame, *, gap_threshold_days: int = GAP_THRESHOLD_DAYS
) -> tuple[str, dict[str, object]]:
    """Classify a bar frame as ``valid`` / ``partial`` / ``invalid`` (docs/11).

    * invalid  — any bar with non-positive or NaN OHLC, or high < low.
    * partial  — a hole larger than ``gap_threshold_days`` between consecutive bars.
    * valid    — everything else.
    """

    if frame is None or frame.empty:
        return "unknown", {"bars": 0}

    ohlc = frame[["open", "high", "low", "close"]].astype(float)
    non_positive = (ohlc <= 0).any(axis=1)
    high_lt_low = frame["high"].astype(float) < frame["low"].astype(float)
    nan_rows = ohlc.isna().any(axis=1)
    invalid_bars = int((non_positive | high_lt_low | nan_rows).sum())

    index = pd.DatetimeIndex(frame.index)
    if len(index) > 1:
        deltas = index.to_series().diff().dt.total_seconds() / 86_400.0
        gaps = int((deltas > gap_threshold_days).sum())
    else:
        gaps = 0

    details: dict[str, object] = {
        "bars": int(len(frame)),
        "invalid_bars": invalid_bars,
        "gaps": gaps,
        "gap_threshold_days": gap_threshold_days,
    }
    if invalid_bars:
        return "invalid", details
    if gaps:
        return "partial", details
    return "valid", details


def bars_to_frame(rows: Iterable[MarketDataBar]) -> pd.DataFrame:
    """Build a UTC-indexed OHLCV DataFrame from ORM rows."""

    records = [
        {
            "timestamp": row.timestamp,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in rows
    ]
    if not records:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(records).set_index("timestamp").sort_index()
    index = pd.DatetimeIndex(frame.index)
    # SQLite drops timezone information; treat naive values as UTC (they are
    # stored in UTC by contract).
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame.index = index.tz_convert("UTC")
    return frame


def frame_to_bars(frame: pd.DataFrame, source_name: str = "local") -> list[dict[str, object]]:
    """Convert a DataFrame into insertable dicts with a per-bar source hash.

    ``is_closed`` comes from the frame when the provider/caller set it (see
    ``mark_closed_bars``); the default is True so historical fixtures behave as
    before. A still-forming bar must be persisted as ``is_closed=False`` rather
    than dropped, so the record exists but strategies ignore it.
    """

    has_closed_flag = "is_closed" in frame.columns
    out: list[dict[str, object]] = []
    for timestamp, row in frame.iterrows():
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        payload = "|".join(
            str(float(row[col])) for col in ("open", "high", "low", "close", "volume")
        )
        out.append(
            {
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "is_closed": bool(row["is_closed"]) if has_closed_flag else True,
                "source_hash": hashlib.sha256(payload.encode()).hexdigest()[:32],
            }
        )
    return out


def get_or_create_series(
    db: Session,
    *,
    asset: Asset,
    timeframe: str,
    source_id: int,
    timezone_name: str = "UTC",
    adjusted: bool = True,
    dataset_version: str = "v1",
) -> MarketDataSeries:
    """Return the series for this combination, creating it when missing."""

    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.asset_id == asset.id,
            MarketDataSeries.timeframe == timeframe,
            MarketDataSeries.source_id == source_id,
            MarketDataSeries.dataset_version == dataset_version,
        )
    )
    if series is None:
        series = MarketDataSeries(
            asset_id=asset.id,
            timeframe=timeframe,
            source_id=source_id,
            timezone=timezone_name,
            adjusted=adjusted,
            dataset_version=dataset_version,
        )
        db.add(series)
        db.flush()
    return series


def load_bars(
    db: Session,
    series: MarketDataSeries,
    *,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    only_closed: bool = True,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load bars into a DataFrame ordered by timestamp ascending."""

    stmt = select(MarketDataBar).where(MarketDataBar.series_id == series.id)
    if start is not None:
        stmt = stmt.where(MarketDataBar.timestamp >= start)
    if end is not None:
        stmt = stmt.where(MarketDataBar.timestamp <= end)
    if only_closed:
        stmt = stmt.where(MarketDataBar.is_closed.is_(True))
    stmt = stmt.order_by(MarketDataBar.timestamp.desc())
    if limit:
        # take the most recent `limit` rows, then restore ascending order
        rows = list(reversed(db.scalars(stmt.limit(limit)).all()))
    else:
        rows = list(db.scalars(stmt.order_by(MarketDataBar.timestamp)).all())
    return bars_to_frame(rows)


def upsert_bars(db: Session, series: MarketDataSeries, bars: list[dict[str, object]]) -> int:
    """Insert bars, skipping timestamps that already exist (idempotent sync)."""

    if not bars:
        return 0

    # Compare in naive-UTC form: SQLite returns naive datetimes, so comparing
    # against tz-aware values would never match and re-insert everything.
    def _naive_utc(value: dt.datetime) -> dt.datetime:
        stamp = pd.Timestamp(value)
        if stamp.tz is not None:
            stamp = stamp.tz_convert("UTC").tz_localize(None)
        return stamp.to_pydatetime()

    timestamps = [bar["timestamp"] for bar in bars]
    naive_keys = [_naive_utc(t) for t in timestamps]  # type: ignore[arg-type]
    existing = {
        _naive_utc(value)
        for value in db.scalars(
            select(MarketDataBar.timestamp).where(
                MarketDataBar.series_id == series.id,
                MarketDataBar.timestamp.in_(naive_keys),
            )
        ).all()
    }
    inserted = 0
    for bar, key in zip(bars, naive_keys, strict=True):
        if key in existing:
            continue
        db.add(MarketDataBar(series_id=series.id, **bar))  # type: ignore[arg-type]
        existing.add(key)
        inserted += 1
    if inserted:
        # SQLite returns naive datetimes; normalise both sides to aware UTC
        # before comparing.
        def _utc(value: dt.datetime | None) -> dt.datetime | None:
            if value is None:
                return None
            return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value

        stamps = [_utc(bar["timestamp"]) for bar in bars]  # type: ignore[arg-type]
        current_start = _utc(series.series_start)
        current_end = _utc(series.series_end)
        series.series_start = min(v for v in (current_start, min(stamps)) if v is not None)
        series.series_end = max(v for v in (current_end, max(stamps)) if v is not None)
        series.last_sync_at = dt.datetime.now(tz=dt.UTC)
    # Whoever writes bars also stamps their hash, so a series can never claim a
    # content hash that its stored bars do not produce (ADR-092).
    refresh_series_content_hash(db, series)
    return inserted


def latest_closed_bar_time(series: MarketDataSeries) -> dt.datetime | None:
    return series.series_end


def refresh_series_content_hash(db: Session, series: MarketDataSeries) -> str | None:
    """Recompute ``market_data.content_hash`` from the bars actually stored.

    The hash covers this series' closed bars in timestamp order — exactly the
    frame a backtest loads when it names no date window — so it equals the
    ``dataset_hash`` recorded by ``POST /backtests`` for the same series. A
    series with no closed bars has no hash at all (``None``), which is not the
    same statement as the hash of an empty frame.
    """

    db.flush()
    frame = load_bars(db, series, only_closed=True)
    series.content_hash = None if frame.empty else series_content_hash(frame)
    return series.content_hash


def series_content_hash(frame: pd.DataFrame) -> str:
    hasher = hashlib.sha256()
    hasher.update(frame.to_csv(float_format="%.10g").encode("utf-8"))
    return hasher.hexdigest()
