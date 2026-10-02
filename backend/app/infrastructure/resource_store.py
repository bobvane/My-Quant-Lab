"""Persistence for collected resource samples: insert, roll up, purge, events."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.models import (
    ContainerResourceSample,
    HostResourceSample,
    ResourceEvent,
    ResourceRollup,
)

logger = logging.getLogger(__name__)

__all__ = [
    "get_peaks_between",
    "latest_sample",
    "purge_expired",
    "record_resource_event",
    "roll_up_recent",
    "save_cycle",
]

_ROLLUP_GRANULARITY = "5m"
_ROLLUP_BUCKET_SECONDS = 300
# Rollups are recomputed from raw samples for all granularities so a chart can
# read the cheapest series that still covers its range (docs/20 §7).
_ROLLUP_GRANULARITIES: dict[str, int] = {"5m": 5, "1h": 60, "1d": 1440}


def save_cycle(db: Session, cycle: dict[str, Any]) -> int:
    """Persist one collection cycle. Returns the number of rows written."""

    now = dt.datetime.now(tz=dt.UTC)
    host = cycle["host"]
    db.add(HostResourceSample(**host))

    rows = 1
    for entry in cycle["containers"]:
        db.add(
            ContainerResourceSample(
                ts=now,
                container_name=entry["container_name"],
                compose_service=entry.get("compose_service"),
                is_quantlab=bool(entry.get("is_quantlab")),
                cpu_percent=entry.get("cpu_percent"),
                mem_used_mb=entry.get("mem_used_mb"),
                mem_limit_mb=entry.get("mem_limit_mb"),
                state=entry.get("state"),
            )
        )
        rows += 1
    db.commit()
    return rows


def roll_up_recent(db: Session) -> int:
    """Recompute the current bucket at every granularity from raw samples.

    Recomputing (rather than incremental averaging) keeps the aggregates free of
    drift when the worker restarts.
    """

    now = dt.datetime.now(tz=dt.UTC)
    written = 0
    for granularity, minutes in _ROLLUP_GRANULARITIES.items():
        bucket_start = now.replace(
            minute=(now.minute // minutes) * minutes if minutes < 60 else 0,
            second=0,
            microsecond=0,
        )
        if minutes >= 1440:
            bucket_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        written += _roll_up_host(db, bucket_start, granularity)
        written += _roll_up_containers(db, bucket_start, granularity)
    db.commit()
    return written


def _roll_up_host(db: Session, bucket_start: dt.datetime, granularity: str) -> int:
    rows = db.scalars(select(HostResourceSample).where(HostResourceSample.ts >= bucket_start)).all()
    if not rows:
        return 0
    cpu_values = [float(r.cpu_percent) for r in rows if r.cpu_percent is not None]
    mem_values = [float(r.mem_used_mb) for r in rows if r.mem_used_mb is not None]
    _upsert_rollup(
        db,
        bucket_start=bucket_start,
        scope="host",
        cpu_avg=_avg(cpu_values),
        cpu_max=_max(cpu_values),
        mem_avg_mb=_avg(mem_values),
        mem_max_mb=_max(mem_values),
        granularity=granularity,
    )
    return 1


def _roll_up_containers(db: Session, bucket_start: dt.datetime, granularity: str) -> int:
    rows = db.scalars(
        select(ContainerResourceSample).where(ContainerResourceSample.ts >= bucket_start)
    ).all()
    by_name: dict[str, list[ContainerResourceSample]] = {}
    for row in rows:
        by_name.setdefault(row.container_name, []).append(row)

    written = 0
    for name, samples in by_name.items():
        cpu_values = [float(s.cpu_percent) for s in samples if s.cpu_percent is not None]
        mem_values = [float(s.mem_used_mb) for s in samples if s.mem_used_mb is not None]
        _upsert_rollup(
            db,
            bucket_start=bucket_start,
            scope=name,
            cpu_avg=_avg(cpu_values),
            cpu_max=_max(cpu_values),
            mem_avg_mb=_avg(mem_values),
            mem_max_mb=_max(mem_values),
            granularity=granularity,
        )
        written += 1

    # Quant Lab aggregate scope, so 7d/30d charts can show "Quant Lab total".
    quantlab_names = {row.container_name for row in rows if row.is_quantlab}
    if quantlab_names:
        quantlab_samples = [s for s in rows if s.container_name in quantlab_names]
        per_ts: dict[dt.datetime, list[ContainerResourceSample]] = {}
        for sample in quantlab_samples:
            per_ts.setdefault(sample.ts, []).append(sample)
        cpu_points: list[float] = []
        mem_points: list[float] = []
        for _ts, samples_at in sorted(per_ts.items()):
            cpu_points.append(sum(float(s.cpu_percent or 0) for s in samples_at))
            mem_points.append(sum(float(s.mem_used_mb or 0) for s in samples_at))
        _upsert_rollup(
            db,
            bucket_start=bucket_start,
            scope="quantlab",
            cpu_avg=_avg(cpu_points),
            cpu_max=_max(cpu_points),
            mem_avg_mb=_avg(mem_points),
            mem_max_mb=_max(mem_points),
            granularity=granularity,
        )
        written += 1
    return written


def _upsert_rollup(
    db: Session,
    *,
    bucket_start: dt.datetime,
    scope: str,
    cpu_avg: float | None,
    cpu_max: float | None,
    mem_avg_mb: float | None,
    mem_max_mb: float | None,
    granularity: str = _ROLLUP_GRANULARITY,
) -> None:
    row = db.scalar(
        select(ResourceRollup).where(
            ResourceRollup.granularity == granularity,
            ResourceRollup.bucket_start == bucket_start,
            ResourceRollup.scope == scope,
        )
    )
    if row is None:
        row = ResourceRollup(granularity=granularity, bucket_start=bucket_start, scope=scope)
        db.add(row)
    row.cpu_avg = cpu_avg
    row.cpu_max = cpu_max
    row.mem_avg_mb = mem_avg_mb
    row.mem_max_mb = mem_max_mb


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _max(values: list[float]) -> float | None:
    return round(max(values), 2) if values else None


def purge_expired(db: Session) -> dict[str, int]:
    """Delete samples/rollups past the configured retention windows."""

    now = dt.datetime.now(tz=dt.UTC)
    raw_cutoff = now - dt.timedelta(days=settings.resource_retention_raw_days)
    rollup_cutoff = now - dt.timedelta(days=settings.resource_retention_rollup_days)

    deleted_host = (
        db.execute(delete(HostResourceSample).where(HostResourceSample.ts < raw_cutoff)).rowcount
        or 0
    )
    deleted_containers = (
        db.execute(
            delete(ContainerResourceSample).where(ContainerResourceSample.ts < raw_cutoff)
        ).rowcount
        or 0
    )
    deleted_rollups = (
        db.execute(
            delete(ResourceRollup).where(ResourceRollup.bucket_start < rollup_cutoff)
        ).rowcount
        or 0
    )
    db.commit()
    return {
        "host_samples": int(deleted_host),
        "container_samples": int(deleted_containers),
        "rollups": int(deleted_rollups),
    }


def latest_sample(db: Session) -> dict[str, Any] | None:
    """Reconstruct the most recent cycle: host row + its container rows."""

    host = db.scalar(select(HostResourceSample).order_by(HostResourceSample.ts.desc()).limit(1))
    if host is None:
        return None
    containers = db.scalars(
        select(ContainerResourceSample)
        .where(ContainerResourceSample.ts == host.ts)
        .order_by(ContainerResourceSample.mem_used_mb.desc())
    ).all()
    # Fall back to the latest sample per container if timestamps differ slightly.
    if not containers:
        containers = _latest_per_container(db)

    quantlab = [c for c in containers if c.is_quantlab]
    quantlab_cpu = round(sum(float(c.cpu_percent or 0) for c in quantlab), 2) if quantlab else None
    quantlab_mem = round(sum(float(c.mem_used_mb or 0) for c in quantlab), 2) if quantlab else None
    host_dict = {
        "ts": host.ts,
        "cpu_percent": float(host.cpu_percent) if host.cpu_percent is not None else None,
        "cpu_count": host.cpu_count,
        "mem_used_mb": float(host.mem_used_mb) if host.mem_used_mb is not None else None,
        "mem_total_mb": float(host.mem_total_mb) if host.mem_total_mb is not None else None,
        "swap_used_mb": float(host.swap_used_mb) if host.swap_used_mb is not None else None,
        "swap_total_mb": float(host.swap_total_mb) if host.swap_total_mb is not None else None,
        "disk_used_gb": float(host.disk_used_gb) if host.disk_used_gb is not None else None,
        "disk_total_gb": float(host.disk_total_gb) if host.disk_total_gb is not None else None,
    }
    shares = _shares(quantlab_cpu, quantlab_mem, host_dict)
    return {
        "host": host_dict,
        "containers": [
            {
                "container_name": c.container_name,
                "compose_service": c.compose_service,
                "is_quantlab": c.is_quantlab,
                "cpu_percent": float(c.cpu_percent) if c.cpu_percent is not None else None,
                "mem_used_mb": float(c.mem_used_mb) if c.mem_used_mb is not None else None,
                "mem_limit_mb": float(c.mem_limit_mb) if c.mem_limit_mb is not None else None,
                "state": c.state,
            }
            for c in containers
        ],
        "quantlab": {
            "cpu": quantlab_cpu,
            "mem_mb": quantlab_mem,
            "container_count": len(quantlab),
            **shares,
        },
    }


def _latest_per_container(db: Session) -> list[ContainerResourceSample]:
    names = db.scalars(select(ContainerResourceSample.container_name).distinct()).all()
    rows: list[ContainerResourceSample] = []
    for name in names:
        row = db.scalar(
            select(ContainerResourceSample)
            .where(ContainerResourceSample.container_name == name)
            .order_by(ContainerResourceSample.ts.desc())
            .limit(1)
        )
        if row is not None:
            rows.append(row)
    return rows


def _shares(
    quantlab_cpu: float | None, quantlab_mem: float | None, host: dict[str, Any]
) -> dict[str, float | None]:
    def ratio(num: float | None, den: float | None) -> float | None:
        if num is None or not den:
            return None
        return round(num / den * 100.0, 2)

    return {
        "cpu_share_of_current_usage": ratio(quantlab_cpu, host.get("cpu_percent")),
        "ram_share_of_used": ratio(quantlab_mem, host.get("mem_used_mb")),
        "ram_share_of_total": ratio(quantlab_mem, host.get("mem_total_mb")),
    }


def record_resource_event(
    db: Session,
    *,
    event_key: str,
    event_type: str,
    started_at: dt.datetime | None = None,
    ended_at: dt.datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> ResourceEvent:
    """Idempotent event upsert: 'started' then 'completed' share one key."""

    row = db.scalar(select(ResourceEvent).where(ResourceEvent.event_key == event_key))
    if row is None:
        row = ResourceEvent(event_key=event_key, event_type=event_type)
        db.add(row)
    row.event_type = event_type
    if started_at is not None:
        row.started_at = started_at
    if ended_at is not None:
        row.ended_at = ended_at
    if row.started_at and row.ended_at:
        row.duration_seconds = int((row.ended_at - row.started_at).total_seconds())
    if payload is not None:
        row.payload_json = payload

    # Peaks come from the sample tables over the event window; a task shorter
    # than one collection cycle leaves them null rather than invented.
    if row.started_at and row.ended_at:
        row.cpu_peak_percent, row.mem_peak_mb = _peaks_between(db, row.started_at, row.ended_at)
    db.flush()
    return row


def _peaks_between(
    db: Session, start: dt.datetime, end: dt.datetime
) -> tuple[float | None, float | None]:
    cpu_max = db.scalar(
        select(func.max(ContainerResourceSample.cpu_percent)).where(
            ContainerResourceSample.ts >= start,
            ContainerResourceSample.ts <= end,
            ContainerResourceSample.is_quantlab.is_(True),
        )
    )
    mem_ts_rows = db.scalars(
        select(ContainerResourceSample).where(
            ContainerResourceSample.ts >= start,
            ContainerResourceSample.ts <= end,
            ContainerResourceSample.is_quantlab.is_(True),
        )
    ).all()
    if not mem_ts_rows:
        return (float(cpu_max) if cpu_max is not None else None), None
    per_ts: dict[dt.datetime, float] = {}
    for row in mem_ts_rows:
        per_ts[row.ts] = per_ts.get(row.ts, 0) + float(row.mem_used_mb or 0)
    mem_peak = max(per_ts.values()) if per_ts else None
    return (
        float(cpu_max) if cpu_max is not None else None,
        round(mem_peak, 2) if mem_peak is not None else None,
    )


def get_peaks_between(
    db: Session, start: dt.datetime, end: dt.datetime
) -> tuple[float | None, float | None]:
    return _peaks_between(db, start, end)
