"""Runtime proxy configuration from system_settings (UI-configurable).

Reads the proxy URL from ``system_settings`` (key: ``proxy_url``). When set,
all outbound HTTP requests to external services (Yahoo Finance, AI providers,
GitHub) go through this proxy. Internal calls (Ghostfolio, PostgreSQL, Redis)
bypass the proxy.

The proxy is never required: when unset, connections are direct.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

__all__ = ["get_proxy_url", "clear_proxy_cache", "PROXY_SETTING_KEY"]

PROXY_SETTING_KEY = "proxy_url"

_cache: dict[str, str | None] = {}
_cache_ts: float = 0
_CACHE_TTL_SECONDS = 30


def get_proxy_url(db: Session) -> str | None:
    """Read the proxy URL from system_settings. Returns None if not set.

    Uses a short-lived cache (30s) to avoid a DB query on every HTTP call.
    """

    import time

    global _cache, _cache_ts

    now = time.time()
    if now - _cache_ts < _CACHE_TTL_SECONDS and PROXY_SETTING_KEY in _cache:
        return _cache[PROXY_SETTING_KEY]

    from sqlalchemy import select

    from app.domain.models import SystemSetting

    try:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == PROXY_SETTING_KEY))
        url = None
        if row and row.value_json and row.value_json.get("value"):
            url = str(row.value_json["value"]).strip() or None
        _cache[PROXY_SETTING_KEY] = url
        _cache_ts = now
        return url
    except Exception:
        logger.warning("failed to read proxy setting", exc_info=True)
        return None


def clear_proxy_cache() -> None:
    """Clear the proxy cache (called when the setting is updated)."""

    global _cache, _cache_ts
    _cache.clear()
    _cache_ts = 0
