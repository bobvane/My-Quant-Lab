"""Notification configuration (M11 / M13, docs/03_MODULES.md).

Configuration lives in ``system_settings`` so it is editable from the UI and
survives restarts, exactly like the runtime proxy setting. Two values are
secrets (the webhook URL may embed a token; the signing secret always is): they
are stored in the settings table and only ever exposed masked.

Alert noise control (docs/09_SIGNAL_ENGINE.md §7) is part of the same block:

* which states are notified (BUY/SELL by default, WAIT optionally),
* quiet hours,
* a daily notification cap,
* a per-symbol cooldown.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import SystemSetting
from app.infrastructure.secrets import decrypt_secret, encrypt_secret
from app.notifications.provider import (
    NotificationConfigError,
    mask_webhook_url,
    validate_webhook_url,
)

__all__ = [
    "NOTIFICATION_FIELDS",
    "NotificationConfig",
    "get_notification_config",
    "quiet_hours_contains",
    "serialize_notification_config",
    "update_notification_config",
]

KEY_ENABLED = "notification_enabled"
KEY_WEBHOOK_URL = "notification_webhook_url"
KEY_WEBHOOK_SECRET = "notification_webhook_secret"
KEY_INCLUDE_WAIT = "notification_include_wait"
KEY_QUIET_HOURS = "notification_quiet_hours"
KEY_DAILY_MAX = "notification_daily_max"
KEY_COOLDOWN_MINUTES = "notification_cooldown_minutes"
KEY_BASE_URL = "notification_base_url"
# Internal watermark (not user-editable): when notifications were last enabled,
# so turning the feature on does not dump months of backlog in one run.
KEY_ENABLED_AT = "notification_enabled_at"

# field name -> (setting key, is_secret, description)
NOTIFICATION_FIELDS: dict[str, tuple[str, bool, str]] = {
    "enabled": (KEY_ENABLED, False, "Send notifications for eligible signals"),
    "webhook_url": (KEY_WEBHOOK_URL, True, "Generic webhook URL (POST, JSON)"),
    "webhook_secret": (KEY_WEBHOOK_SECRET, True, "Optional HMAC-SHA256 signing secret"),
    "include_wait": (KEY_INCLUDE_WAIT, False, "Also notify WAIT signals"),
    "quiet_hours": (KEY_QUIET_HOURS, False, "Quiet window, e.g. 22:00-07:00 (UTC)"),
    "daily_max": (KEY_DAILY_MAX, False, "Max notifications per day (0 = unlimited)"),
    "cooldown_minutes": (KEY_COOLDOWN_MINUTES, False, "Min minutes between alerts per series"),
    "base_url": (KEY_BASE_URL, False, "Public base URL used to build signal links"),
}

ALL_KEYS = [meta[0] for meta in NOTIFICATION_FIELDS.values()] + [KEY_ENABLED_AT]
_KEY_TO_FIELD = {meta[0]: field for field, meta in NOTIFICATION_FIELDS.items()}
_SECRET_FIELDS = {field for field, meta in NOTIFICATION_FIELDS.items() if meta[1]}


@dataclass(frozen=True)
class NotificationConfig:
    """Immutable snapshot of the notification settings."""

    enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    include_wait: bool = False
    quiet_hours: str = ""
    daily_max: int = 0
    cooldown_minutes: int = 0
    base_url: str = ""
    enabled_at: dt.datetime | None = None

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.webhook_url)

    @property
    def eligible_states(self) -> tuple[str, ...]:
        states = ["BUY", "SELL"]
        if self.include_wait:
            states.append("WAIT")
        return tuple(states)

    def is_quiet(self, moment: dt.datetime) -> bool:
        return quiet_hours_contains(self.quiet_hours, moment)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_clock(text: str) -> dt.time | None:
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return dt.time(hour=hour, minute=minute)


def _validate_quiet_hours(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parts = text.split("-")
    if len(parts) != 2 or _parse_clock(parts[0]) is None or _parse_clock(parts[1]) is None:
        raise NotificationConfigError("quiet_hours must look like '22:00-07:00'")
    return text


def quiet_hours_contains(text: str, moment: dt.datetime) -> bool:
    """Whether ``moment`` (UTC) falls inside the quiet window.

    The window may cross midnight (``22:00-07:00``). An unparseable window is
    treated as "never quiet" rather than silently suppressing every alert.
    """

    if not text:
        return False
    parts = text.split("-")
    if len(parts) != 2:
        return False
    start, end = _parse_clock(parts[0]), _parse_clock(parts[1])
    if start is None or end is None:
        return False
    now = moment.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _decode_secret(value: Any) -> str:
    """Decrypt a stored secret, tolerating plaintext rows from before encryption."""

    text = str(value or "")
    if not text:
        return ""
    try:
        return decrypt_secret(text)
    except Exception:  # noqa: BLE001 - legacy/plaintext value, use as-is
        return text


def _parse_enabled_at(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def get_notification_config(db: Session) -> NotificationConfig:
    rows = db.scalars(select(SystemSetting).where(SystemSetting.key.in_(ALL_KEYS))).all()
    values: dict[str, Any] = {}
    enabled_at_raw: Any = None
    for row in rows:
        raw = (row.value_json or {}).get("value")
        if row.key == KEY_ENABLED_AT:
            enabled_at_raw = raw
        elif row.key in _KEY_TO_FIELD:
            values[_KEY_TO_FIELD[row.key]] = raw
    return NotificationConfig(
        enabled=_as_bool(values.get("enabled")),
        webhook_url=_decode_secret(values.get("webhook_url")),
        webhook_secret=_decode_secret(values.get("webhook_secret")),
        include_wait=_as_bool(values.get("include_wait")),
        quiet_hours=str(values.get("quiet_hours") or "").strip(),
        daily_max=max(0, _as_int(values.get("daily_max"))),
        cooldown_minutes=max(0, _as_int(values.get("cooldown_minutes"))),
        base_url=str(values.get("base_url") or "").strip(),
        enabled_at=_parse_enabled_at(enabled_at_raw),
    )


def _coerce(field: str, value: Any) -> Any:
    if field in {"enabled", "include_wait"}:
        return _as_bool(value)
    if field in {"daily_max", "cooldown_minutes"}:
        number = _as_int(value, default=-1)
        if number < 0:
            raise NotificationConfigError(f"{field} must be a non-negative integer")
        return number
    if field == "webhook_url":
        return validate_webhook_url(str(value or ""))
    if field == "base_url":
        text = str(value or "").strip().rstrip("/")
        if text and not text.startswith(("http://", "https://")):
            raise NotificationConfigError("base_url must start with http:// or https://")
        return text
    if field == "quiet_hours":
        return _validate_quiet_hours(str(value or ""))
    return str(value or "").strip()


def update_notification_config(db: Session, changes: Mapping[str, Any]) -> NotificationConfig:
    """Validate and persist the provided fields, leaving the rest untouched."""

    unknown = set(changes) - set(NOTIFICATION_FIELDS)
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise NotificationConfigError(f"unknown notification field(s): {joined}")

    current = get_notification_config(db)
    for field, raw in changes.items():
        value = _coerce(field, raw)
        key, is_secret, description = NOTIFICATION_FIELDS[field]
        stored: Any = value
        if field in _SECRET_FIELDS and value:
            # Secrets are encrypted at rest, unlike the plain noise-control keys.
            stored = encrypt_secret(str(value))
        _upsert(db, key, stored, is_secret=is_secret, description=description)

    # Watermark so enabling the feature never dumps a backlog of old signals.
    if "enabled" in changes:
        if _as_bool(changes["enabled"]) and not current.enabled:
            _upsert(
                db,
                KEY_ENABLED_AT,
                dt.datetime.now(tz=dt.UTC).isoformat(),
                is_secret=False,
                description="Set when notifications were last enabled",
            )
        elif not _as_bool(changes["enabled"]):
            _upsert(
                db,
                KEY_ENABLED_AT,
                None,
                is_secret=False,
                description="Set when notifications were last enabled",
            )
    db.flush()
    return get_notification_config(db)


def _upsert(db: Session, key: str, value: Any, *, is_secret: bool, description: str | None) -> None:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        row = SystemSetting(key=key, is_secret=is_secret)
        db.add(row)
    row.is_secret = is_secret
    row.description = description
    row.value_json = None if value in (None, "") else {"value": value}


def serialize_notification_config(config: NotificationConfig) -> dict[str, Any]:
    """Public shape: secrets masked, plus an explicit ``configured`` flag."""

    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "webhook_url_set": bool(config.webhook_url),
        "webhook_url_masked": mask_webhook_url(config.webhook_url),
        "webhook_secret_set": bool(config.webhook_secret),
        "include_wait": config.include_wait,
        "quiet_hours": config.quiet_hours,
        "daily_max": config.daily_max,
        "cooldown_minutes": config.cooldown_minutes,
        "base_url": config.base_url,
        "enabled_at": config.enabled_at,
        "eligible_states": list(config.eligible_states),
    }
