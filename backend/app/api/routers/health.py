"""Health and system information endpoints."""

from __future__ import annotations

import logging

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


def _check_database(db: Session) -> str:
    try:
        db.execute(text("SELECT 1"))
        return "connected"
    except Exception:  # pragma: no cover - depends on runtime
        logger.warning("database health check failed", exc_info=True)
        return "unavailable"


def _check_redis() -> str:
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        client.ping()
        return "connected"
    except Exception:
        return "unavailable"


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
        "workers": "unknown",
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
