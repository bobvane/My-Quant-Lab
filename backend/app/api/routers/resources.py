"""System Resource Monitor endpoints.

Read-only views over the sample tables. The browser is expected to poll these
every 30-60s; the collection itself happens once a minute in the worker, so
there is no high-frequency request path anywhere.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import (
    ContainerResourceSample,
    HostResourceSample,
    ResourceEvent,
    ResourceRollup,
)
from app.infrastructure.resource_store import latest_sample

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resources", tags=["resources"])

_RANGES = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}


@router.get("/current", summary="Latest sample: NAS overview + Quant Lab shares + containers")
def current(db: Session = Depends(get_db)) -> dict[str, Any]:
    payload = latest_sample(db)
    if payload is None:
        return {
            "available": False,
            "note": (
                "no samples collected yet — the collector runs once a minute in the "
                "worker (enable RESOURCE_COLLECTION_ENABLED and keep the worker running)"
            ),
            "host": None,
            "containers": [],
            "quantlab": None,
        }
    return {"available": True, **payload}


@router.get("/history", summary="CPU/RAM time series for 1h/24h/7d/30d")
def history(
    db: Session = Depends(get_db),
    metric: str = Query(default="cpu", pattern="^(cpu|ram)$"),
    range: str = Query(default="24h", pattern="^(1h|24h|7d|30d)$"),
) -> dict[str, Any]:
    hours = _RANGES[range]
    since = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=hours)
    use_raw = hours <= 24

    host_points: list[dict[str, Any]] = []
    quantlab_points: list[dict[str, Any]] = []
    container_points: dict[str, list[dict[str, Any]]] = {}

    if use_raw:

        def pick(value: float | None) -> float | None:
            if value is None:
                return None
            return float(value)

        for row in db.scalars(
            select(HostResourceSample)
            .where(HostResourceSample.ts >= since)
            .order_by(HostResourceSample.ts)
        ).all():
            value = pick(row.cpu_percent) if metric == "cpu" else pick(row.mem_used_mb)
            host_points.append({"ts": row.ts.isoformat(), "value": value})
        for row in db.scalars(
            select(ContainerResourceSample)
            .where(ContainerResourceSample.ts >= since)
            .order_by(ContainerResourceSample.ts)
        ).all():
            value = pick(row.cpu_percent) if metric == "cpu" else pick(row.mem_used_mb)
            key = "quantlab" if row.is_quantlab else row.container_name
            container_points.setdefault(key, []).append({"ts": row.ts.isoformat(), "value": value})
    else:
        for row in db.scalars(
            select(ResourceRollup)
            .where(
                ResourceRollup.granularity == "5m",
                ResourceRollup.bucket_start >= since,
            )
            .order_by(ResourceRollup.bucket_start)
        ).all():
            cpu = float(row.cpu_avg) if row.cpu_avg is not None else None
            mem = float(row.mem_avg_mb) if row.mem_avg_mb is not None else None
            value = cpu if metric == "cpu" else mem
            point = {"ts": row.bucket_start.isoformat(), "value": value}
            if row.scope == "host":
                host_points.append(point)
            elif row.scope == "quantlab":
                quantlab_points.append(point)
            else:
                container_points.setdefault(row.scope, []).append(point)

    return {
        "metric": metric,
        "range": range,
        "source": "raw" if use_raw else "5m_rollup",
        "host": host_points,
        "quantlab": quantlab_points,
        "containers": container_points,
    }


@router.get("/events", summary="Task resource events (backtest peaks etc.)")
def events(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    rows = db.scalars(select(ResourceEvent).order_by(ResourceEvent.id.desc()).limit(limit)).all()
    return {
        "events": [
            {
                "event_key": r.event_key,
                "event_type": r.event_type,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "duration_seconds": r.duration_seconds,
                "cpu_peak_percent": float(r.cpu_peak_percent)
                if r.cpu_peak_percent is not None
                else None,
                "mem_peak_mb": float(r.mem_peak_mb) if r.mem_peak_mb is not None else None,
                "payload": r.payload_json,
            }
            for r in rows
        ]
    }


@router.get("/summary", summary="Direct answers: NAS total, Quant Lab share, top consumers")
def summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    payload = latest_sample(db)
    if payload is None:
        return {"available": False}

    containers = payload["containers"]
    top_overall = max(
        (c for c in containers if c["mem_used_mb"] is not None),
        key=lambda c: c["mem_used_mb"],
        default=None,
    )
    quantlab_containers = [c for c in containers if c["is_quantlab"]]
    top_quantlab = max(
        (c for c in quantlab_containers if c["mem_used_mb"] is not None),
        key=lambda c: c["mem_used_mb"],
        default=None,
    )

    week_ago = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=7)
    idle_rows = db.scalars(
        select(ResourceRollup).where(
            ResourceRollup.scope == "quantlab",
            ResourceRollup.granularity == "5m",
            ResourceRollup.bucket_start >= week_ago,
        )
    ).all()
    idle_cpu = [float(r.cpu_avg) for r in idle_rows if r.cpu_avg is not None]
    idle_mem = [float(r.mem_avg_mb) for r in idle_rows if r.mem_avg_mb is not None]

    return {
        "available": True,
        "nas": {
            "cpu_percent": payload["host"]["cpu_percent"],
            "cpu_count": payload["host"]["cpu_count"],
            "ram_used_mb": payload["host"]["mem_used_mb"],
            "ram_total_mb": payload["host"]["mem_total_mb"],
            "disk_used_gb": payload["host"]["disk_used_gb"],
            "disk_total_gb": payload["host"]["disk_total_gb"],
        },
        "quantlab": {
            "cpu_percent": payload["quantlab"]["cpu"],
            "ram_used_mb": payload["quantlab"]["mem_mb"],
            "container_count": payload["quantlab"].get("container_count"),
            "ram_share_of_used": payload["quantlab"].get("ram_share_of_used"),
            "ram_share_of_total": payload["quantlab"].get("ram_share_of_total"),
            "cpu_share_of_current_usage": payload["quantlab"].get("cpu_share_of_current_usage"),
        },
        "top_ram_overall": (
            {"name": top_overall["container_name"], "mem_used_mb": top_overall["mem_used_mb"]}
            if top_overall
            else None
        ),
        "top_ram_quantlab": (
            {"name": top_quantlab["container_name"], "mem_used_mb": top_quantlab["mem_used_mb"]}
            if top_quantlab
            else None
        ),
        "idle_7d": {
            "cpu_avg_percent": round(sum(idle_cpu) / len(idle_cpu), 2) if idle_cpu else None,
            "ram_avg_mb": round(sum(idle_mem) / len(idle_mem), 2) if idle_mem else None,
            "samples": len(idle_cpu),
        },
    }
