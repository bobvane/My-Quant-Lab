"""Health and system information endpoints."""

from __future__ import annotations

import contextlib
import logging
import threading

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.features.engine import FEATURE_VERSION
from app.research.engine import ENGINE_VERSION
from app.strategies.dsl import SCHEMA_VERSION

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

#: Upper bound, in seconds, for every dependency probe in this module. `/health`
#: is read by the dashboard, by container health checks and by deployment smoke
#: tests: a probe that answers after the caller gave up is not an answer, so each
#: one gets a deadline instead of the driver's default (ADR-069).
PROBE_TIMEOUT_SECONDS = 1.0


def _check_database(db: Session) -> str:
    try:
        db.execute(text("SELECT 1"))
        return "connected"
    except Exception:  # pragma: no cover - depends on runtime
        logger.warning("database health check failed", exc_info=True)
        return "unavailable"


def _ensure_broker_reachable() -> None:
    """Open (and immediately drop) a broker connection, bounded in time.

    ``control.ping(timeout=...)`` bounds how long we wait for *replies*, not how
    long reaching the broker may take: with no broker running at all the host does
    not even resolve, and the ping sat there for eleven seconds before reporting
    ``unknown``. Asking the connection question first — explicit connect timeout,
    no retries — turns that into a bounded answer (ADR-069).

    Raises on an unreachable broker; the caller turns that into ``unknown``.
    """

    from app.workers.celery_app import celery_app

    connection = celery_app.connection_for_read(
        transport_options={
            "socket_connect_timeout": PROBE_TIMEOUT_SECONDS,
            "socket_timeout": PROBE_TIMEOUT_SECONDS,
        }
    )
    try:
        connection.ensure_connection(max_retries=0, timeout=PROBE_TIMEOUT_SECONDS)
    finally:
        # Closing a connection that just failed must not become the error we report.
        with contextlib.suppress(Exception):
            connection.release()


def _check_workers() -> str:
    """Best-effort ping of the Celery workers (never fatal for liveness).

    Two bounded steps on purpose: first "is the broker reachable at all", then
    "is anyone answering on it". Both are what keep the word ``unknown`` cheap.
    """

    try:
        _ensure_broker_reachable()

        from app.workers.celery_app import celery_app

        replies = celery_app.control.ping(timeout=PROBE_TIMEOUT_SECONDS) or []
        return f"{len(replies)} online" if replies else "0 online"
    except Exception:
        return "unknown"


def _check_redis() -> str:
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
        )
        client.ping()
        return "connected"
    except Exception:
        return "unavailable"


def warm_dependency_probes() -> threading.Thread:
    """Run the dependency probes once, off the request path, and keep nothing.

    The *first* probe in a process pays for a cold resolver and for the broker
    transport the ping needs; that part is not covered by any socket timeout, so it
    is not a deadline we can set — but it is a cost nobody has to see on the first
    request. The results are discarded: `/health` still measures live, so warming
    can never turn into a stale "connected" (ADR-069).
    """

    def _warm() -> None:
        for probe in (_check_redis, _check_workers):
            with contextlib.suppress(Exception):
                probe()

    thread = threading.Thread(target=_warm, name="health-probe-warmup", daemon=True)
    thread.start()
    return thread


@router.get("/healthz", include_in_schema=False)
def liveness() -> dict:
    """Liveness probe: touches nothing.

    Container health checks must not depend on PostgreSQL or Redis — a
    dependency blip must not make the API container look dead and tear down
    healthy dependents. Dependency state is reported by ``/health``.
    """

    return {"status": "alive"}


@router.get("/health", summary="Liveness and dependency health")
def health(db: Session = Depends(get_db)) -> dict:
    database = _check_database(db)
    redis_state = _check_redis()
    status = "healthy" if database == "connected" else "degraded"
    return {
        "status": status,
        "version": settings.app_version,
        "database": database,
        "redis": redis_state,
        "workers": _check_workers(),
        "environment": settings.environment,
        "feature_version": FEATURE_VERSION,
        "engine_version": ENGINE_VERSION,
    }


@router.get("/system/info", summary="Runtime and engine versions")
def system_info() -> dict:
    return {
        "app_name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "market_data_provider": settings.market_data_provider,
        "default_currency": settings.default_currency,
        "default_timezone": settings.default_timezone,
        "feature_version": FEATURE_VERSION,
        "engine_version": ENGINE_VERSION,
        "strategy_schema_version": SCHEMA_VERSION,
        "modules": [
            "domain",
            "data",
            "features",
            "strategies",
            "research",
            "simulation",
            "ai",
            "infrastructure",
        ],
    }
