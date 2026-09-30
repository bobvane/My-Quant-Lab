"""System Resource Monitor collector (docs/20_RESOURCE_MONITOR.md).

Layer 1 (no Docker access): NAS-wide metrics via psutil — inside a container
``/proc/stat`` and ``/proc/meminfo`` reflect the *host* by default, which is
exactly what we want. Quant Lab's own containers report through their cgroups.

Layer 2 (optional): every container on the NAS, via the read-only stats proxy.
Enabled by setting ``docker_proxy_url``; without it the collector degrades to
layer 1 and the Docker Overview table is simply absent — never fabricated.

Design constraint: the monitor must not become a resource burden. One cycle is
a handful of cheap reads plus a couple of small inserts.
"""

from __future__ import annotations

import datetime as dt
import logging
import pathlib
from typing import Any

import psutil

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "collect_cycle",
    "collect_container_metrics",
    "collect_host_metrics",
    "collect_own_container_metrics",
    "quantlab_shares",
]

BYTES_PER_MB = 1024 * 1024
BYTES_PER_GB = 1024 * 1024 * 1024


def collect_host_metrics(disk_paths: list[str] | None = None) -> dict[str, Any]:
    """NAS-wide CPU / memory / swap / disk.

    psutil reads /proc, which inside a container reports host-wide values for
    cpu/mem/loadavg (nothing here is namespaced by default).
    """

    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    disk_used_gb = disk_total_gb = None
    for path in disk_paths or settings.resource_disk_paths:
        try:
            usage = psutil.disk_usage(path)
        except Exception:  # pragma: no cover - path may not exist in container
            continue
        disk_used_gb = (usage.total - usage.free) / BYTES_PER_GB
        disk_total_gb = usage.total / BYTES_PER_GB
        break

    return {
        "ts": dt.datetime.now(tz=dt.UTC),
        "cpu_percent": round(cpu_percent, 2),
        "cpu_count": psutil.cpu_count() or 1,
        "mem_used_mb": round((mem.total - mem.available) / BYTES_PER_MB, 2),
        "mem_total_mb": round(mem.total / BYTES_PER_MB, 2),
        "swap_used_mb": round(swap.used / BYTES_PER_MB, 2) if swap.total else 0.0,
        "swap_total_mb": round(swap.total / BYTES_PER_MB, 2) if swap.total else 0.0,
        "disk_used_gb": round(disk_used_gb, 2) if disk_used_gb is not None else None,
        "disk_total_gb": round(disk_total_gb, 2) if disk_total_gb is not None else None,
    }


def _read_cgroup_v2_memory() -> dict[str, float] | None:
    try:
        current = int(pathlib.Path("/sys/fs/cgroup/memory.current").read_text())
        limit_text = pathlib.Path("/sys/fs/cgroup/memory.max").read_text().strip()
        limit = None if limit_text == "max" else int(limit_text)
        return {"used_mb": current / BYTES_PER_MB, "limit_mb": (limit or 0) / BYTES_PER_MB}
    except Exception:
        return None


def _read_cgroup_v2_cpu_percent(previous: dict[str, float] | None) -> float | None:
    """CPU% from cgroup v2 ``cpu.stat`` using the previous sample as baseline."""

    try:
        text = pathlib.Path("/sys/fs/cgroup/cpu.stat").read_text()
    except Exception:
        return None
    fields = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            fields[parts[0]] = float(parts[1])
    usage_usec = fields.get("usage_usec")
    if usage_usec is None:
        return None
    now = dt.datetime.now(tz=dt.UTC).timestamp()
    if not previous:
        return None
    delta_usec = usage_usec - previous.get("usage_usec", 0)
    delta_seconds = now - previous.get("ts", 0)
    if delta_seconds <= 0 or delta_usec < 0:
        return None
    # usage_usec is summed across cores; divide by wall time for overall %.
    return round(min(100.0 * (delta_usec / 1e6) / delta_seconds, 100.0 * psutil.cpu_count()), 2)


def collect_own_container_metrics(previous: dict[str, float] | None) -> dict[str, Any]:
    """This container's own stats via its cgroup (no Docker access needed)."""

    mem = _read_cgroup_v2_memory()
    payload: dict[str, Any] = {
        "container_name": "self",
        "cpu_percent": _read_cgroup_v2_cpu_percent(previous),
        "mem_used_mb": round(mem["used_mb"], 2) if mem else None,
        "mem_limit_mb": round(mem["limit_mb"], 2) if mem and mem.get("limit_mb") else None,
        "state": "running",
    }
    cgroup_cpu = {
        "usage_usec": _read_cgroup_cpu_usage_usec(),
        "ts": dt.datetime.now(tz=dt.UTC).timestamp(),
    }
    return payload, cgroup_cpu


def _read_cgroup_cpu_usage_usec() -> float | None:
    try:
        for line in pathlib.Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines():
            if line.startswith("usage_usec "):
                return float(line.split()[1])
    except Exception:
        return None
    return None


def _proxy_headers() -> dict[str, str]:
    token = settings.docker_proxy_token or ""
    return {"X-QuantLab-Token": token} if token else {}


def collect_container_metrics() -> list[dict[str, Any]]:
    """Every container on the NAS via the read-only proxy (phase 1b).

    ``stream=false`` (no ``one-shot``) makes the Docker daemon compute a real
    CPU delta, so the percentages are meaningful. Uses a thread pool because
    each stats call takes the daemon roughly a second.
    """

    if not settings.docker_proxy_url:
        return []

    import concurrent.futures

    import httpx

    base = settings.docker_proxy_url.rstrip("/")
    headers = _proxy_headers()
    try:
        response = httpx.get(f"{base}/containers/json?all=1", headers=headers, timeout=10.0)
        response.raise_for_status()
        listing = response.json()
    except Exception as exc:
        logger.warning("docker proxy list failed: %s", exc)
        return []

    def fetch(entry: dict[str, Any]) -> dict[str, Any] | None:
        cid = entry.get("Id")
        if not cid:
            return None
        names = entry.get("Names") or []
        name = names[0].lstrip("/") if names else cid[:12]
        labels = entry.get("Labels") or {}
        is_quantlab = labels.get("com.docker.compose.project") == settings.quantlab_compose_project
        service = labels.get("com.docker.compose.service")
        state = entry.get("State")
        try:
            stats_response = httpx.get(
                f"{base}/containers/{cid}/stats?stream=false", headers=headers, timeout=15.0
            )
            stats_response.raise_for_status()
            stats = stats_response.json()
        except Exception as exc:
            logger.warning("docker proxy stats failed for %s: %s", name, exc)
            return {
                "container_name": name,
                "compose_service": service,
                "is_quantlab": is_quantlab,
                "cpu_percent": None,
                "mem_used_mb": None,
                "mem_limit_mb": None,
                "state": state,
            }

        cpu = _stats_cpu_percent(stats)
        mem_used = float(stats.get("memory_stats", {}).get("usage") or 0) / BYTES_PER_MB
        mem_limit = float(stats.get("memory_stats", {}).get("limit") or 0) / BYTES_PER_MB
        return {
            "container_name": name,
            "compose_service": service,
            "is_quantlab": is_quantlab,
            "cpu_percent": cpu,
            "mem_used_mb": round(mem_used, 2),
            "mem_limit_mb": round(mem_limit, 2) if mem_limit else None,
            "state": state,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(fetch, listing) if r is not None]
    return rows


def _stats_cpu_percent(stats: dict[str, Any]) -> float | None:
    """Docker's own formula; with stream=false the daemon supplies a real delta."""

    cpu = stats.get("cpu_stats", {}) or {}
    precpu = stats.get("precpu_stats", {}) or {}
    total = float(cpu.get("cpu_usage", {}).get("total_usage") or 0)
    prev = float(precpu.get("cpu_usage", {}).get("total_usage") or 0)
    system = float(cpu.get("system_cpu_usage") or 0)
    presystem = float(precpu.get("system_cpu_usage") or 0)
    online = float(cpu.get("online_cpus") or psutil.cpu_count() or 1)

    cpu_delta = total - prev
    system_delta = system - presystem
    if system_delta <= 0 or cpu_delta < 0:
        return None
    return round(min((cpu_delta / system_delta) * online * 100.0, online * 100.0), 2)


def quantlab_shares(
    quantlab_cpu: float | None,
    quantlab_mem_mb: float | None,
    host: dict[str, Any],
) -> dict[str, float | None]:
    """Documented share formulas (see docs/20_RESOURCE_MONITOR.md section 3)."""

    host_used_mb = host.get("mem_used_mb")
    host_total_mb = host.get("mem_total_mb")
    host_cpu = host.get("cpu_percent")

    def ratio(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or not denominator:
            return None
        return round(numerator / denominator * 100.0, 2)

    return {
        "cpu_share_of_current_usage": ratio(quantlab_cpu, host_cpu),
        "ram_share_of_used": ratio(quantlab_mem_mb, host_used_mb),
        "ram_share_of_total": ratio(quantlab_mem_mb, host_total_mb),
    }


def collect_cycle() -> dict[str, Any]:
    """One full collection cycle: host + own container + (optional) all containers."""

    host = collect_host_metrics()
    own_payload, _ = collect_own_container_metrics(None)
    rows = collect_container_metrics()

    if rows:
        # The collector runs inside the worker; proxy data should include it.
        if not any(r["container_name"] == own_payload["container_name"] for r in rows):
            rows.append(own_payload)
    else:
        rows = [own_payload]

    quantlab_rows = [r for r in rows if r.get("is_quantlab")]
    quantlab_cpu = (
        round(sum(float(r["cpu_percent"] or 0) for r in quantlab_rows), 2)
        if quantlab_rows
        else None
    )
    quantlab_mem = (
        round(sum(float(r["mem_used_mb"] or 0) for r in quantlab_rows), 2)
        if quantlab_rows
        else None
    )
    shares = quantlab_shares(quantlab_cpu, quantlab_mem, host)
    return {
        "host": host,
        "containers": rows,
        "quantlab": {"cpu": quantlab_cpu, "mem_mb": quantlab_mem, **shares},
    }
