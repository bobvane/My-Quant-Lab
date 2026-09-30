"""Notification configuration (M11 / M13, docs/03_MODULES.md).

Configuration lives in ``system_settings`` so it is editable from the UI and
survives restarts, exactly like the runtime proxy setting.

Shape:

* **global**: on/off, which states are notified, quiet hours, daily cap,
  per-series cooldown, optional base URL for links;
* **channels**: one or more delivery destinations (webhook / feishu / telegram
  / pushplus / email). Secret fields are encrypted at rest and only ever
  exposed masked.

The original single-webhook keys (``notification_webhook_url`` /
``notification_webhook_secret``) are still read for backward compatibility: if
no channel list is stored, a webhook channel is synthesized from them.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import SystemSetting
from app.infrastructure.secrets import decrypt_secret, encrypt_secret
from app.notifications.channels import (
    NotificationChannel,
    channel_from_storage,
    channel_to_storage,
    normalize_channel,
    serialize_channel,
)
from app.notifications.provider import NotificationConfigError, validate_webhook_url

logger = logging.getLogger(__name__)

__all__ = [
    "NOTIFICATION_FIELDS",
    "NotificationConfig",
    "get_notification_config",
    "quiet_hours_contains",
    "serialize_notification_config",
    "update_notification_config",
]

KEY_ENABLED = "notification_enabled"
KEY_INCLUDE_WAIT = "notification_include_wait"
KEY_QUIET_HOURS = "notification_quiet_hours"
KEY_DAILY_MAX = "notification_daily_max"
KEY_COOLDOWN_MINUTES = "notification_cooldown_minutes"
KEY_BASE_URL = "notification_base_url"
KEY_CHANNELS = "notification_channels"
# Legacy single-webhook keys, still read for backward compatibility.
KEY_WEBHOOK_URL = "notification_webhook_url"
KEY_WEBHOOK_SECRET = "notification_webhook_secret"
# Internal watermark (not user-editable).
KEY_ENABLED_AT = "notification_enabled_at"

# Global (channel-independent) field name -> (setting key, description)
NOTIFICATION_FIELDS: dict[str, tuple[str, str]] = {
    "enabled": (KEY_ENABLED, "Send notifications for eligible signals"),
    "include_wait": (KEY_INCLUDE_WAIT, "Also notify WAIT signals"),
    "quiet_hours": (KEY_QUIET_HOURS, "Quiet window, e.g. 22:00-07:00 (UTC)"),
    "daily_max": (KEY_DAILY_MAX, "Max notifications per day (0 = unlimited)"),
    "cooldown_minutes": (KEY_COOLDOWN_MINUTES, "Min minutes between alerts per series"),
    "base_url": (KEY_BASE_URL, "Public base URL used to build signal links"),
}
ALL_KEYS = [key for key, _ in NOTIFICATION_FIELDS.values()] + [
    KEY_CHANNELS,
    KEY_WEBHOOK_URL,
    KEY_WEBHOOK_SECRET,
    KEY_ENABLED_AT,
]


@dataclass(frozen=True)
class NotificationConfig:
    """Immutable snapshot of the notification settings."""

    enabled: bool = False
    include_wait: bool = False
    quiet_hours: str = ""
    daily_max: int = 0
    cooldown_minutes: int = 0
    base_url: str = ""
    channels: tuple[NotificationChannel, ...] = ()
    enabled_at: dt.datetime | None = None

    @property
    def enabled_channels(self) -> tuple[NotificationChannel, ...]:
        return tuple(channel for channel in self.channels if channel.enabled)

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.enabled_channels)

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


def _load_channels(raw_channels: Any) -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []
    if isinstance(raw_channels, list):
        for item in raw_channels:
            if not isinstance(item, dict):
                continue
            try:
                channels.append(channel_from_storage(item))
            except NotificationConfigError:
                logger.warning("ignoring malformed stored notification channel")
    return channels


def _legacy_webhook_channel(values: dict[str, Any]) -> list[NotificationChannel]:
    url = _decode_secret(values.get(KEY_WEBHOOK_URL))
    if not url:
        return []
    return [
        NotificationChannel(
            id="webhook",
            type="webhook",
            enabled=True,
            config={"url": url, "secret": _decode_secret(values.get(KEY_WEBHOOK_SECRET))},
        )
    ]


def get_notification_config(db: Session) -> NotificationConfig:
    rows = db.scalars(select(SystemSetting).where(SystemSetting.key.in_(ALL_KEYS))).all()
    values: dict[str, Any] = {row.key: (row.value_json or {}).get("value") for row in rows}

    channels = _load_channels(values.get(KEY_CHANNELS))
    if not channels:
        channels = _legacy_webhook_channel(values)

    return NotificationConfig(
        enabled=_as_bool(values.get(KEY_ENABLED)),
        include_wait=_as_bool(values.get(KEY_INCLUDE_WAIT)),
        quiet_hours=str(values.get(KEY_QUIET_HOURS) or "").strip(),
        daily_max=max(0, _as_int(values.get(KEY_DAILY_MAX))),
        cooldown_minutes=max(0, _as_int(values.get(KEY_COOLDOWN_MINUTES))),
        base_url=str(values.get(KEY_BASE_URL) or "").strip(),
        channels=tuple(channels),
        enabled_at=_parse_enabled_at(values.get(KEY_ENABLED_AT)),
    )


def _coerce(field: str, value: Any) -> Any:
    if field in {"enabled", "include_wait"}:
        return _as_bool(value)
    if field in {"daily_max", "cooldown_minutes"}:
        number = _as_int(value, default=-1)
        if number < 0:
            raise NotificationConfigError(f"{field} must be a non-negative integer")
        return number
    if field == "base_url":
        text = str(value or "").strip().rstrip("/")
        if text and not text.startswith(("http://", "https://")):
            raise NotificationConfigError("base_url must start with http:// or https://")
        return text
    if field == "quiet_hours":
        return _validate_quiet_hours(str(value or ""))
    return str(value or "").strip()


def _coerce_channels(raw: Any, previous: list[NotificationChannel]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise NotificationConfigError("channels must be a list")
    previous_by_id = {channel.id: channel for channel in previous}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise NotificationConfigError("each channel must be an object")
        channel = normalize_channel(item, previous=previous_by_id.get(str(item.get("id") or "")))
        if channel.id in seen:
            raise NotificationConfigError(f"duplicate channel id '{channel.id}'")
        seen.add(channel.id)
        normalized.append(channel_to_storage(channel))
    return normalized


def update_notification_config(db: Session, changes: Mapping[str, Any]) -> NotificationConfig:
    """Validate and persist the provided fields, leaving the rest untouched."""

    allowed = set(NOTIFICATION_FIELDS) | {"channels", "webhook_url", "webhook_secret"}
    unknown = set(changes) - allowed
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise NotificationConfigError(f"unknown notification field(s): {joined}")

    current = get_notification_config(db)
    for field, raw in changes.items():
        if field in NOTIFICATION_FIELDS:
            key, description = NOTIFICATION_FIELDS[field]
            _upsert(
                db,
                key,
                _coerce(field, raw),
                is_secret=False,
                description=description,
            )

    if "channels" in changes:
        stored = _coerce_channels(changes["channels"], list(current.channels))
        _upsert(db, KEY_CHANNELS, stored, is_secret=True, description="Notification channels")
        # The channel list supersedes the legacy single-webhook keys.
        _upsert(db, KEY_WEBHOOK_URL, None, is_secret=True, description="Legacy webhook URL")
        _upsert(db, KEY_WEBHOOK_SECRET, None, is_secret=True, description="Legacy webhook secret")

    if "webhook_url" in changes:
        value = validate_webhook_url(str(changes["webhook_url"] or ""))
        _upsert(
            db,
            KEY_WEBHOOK_URL,
            encrypt_secret(value) if value else None,
            is_secret=True,
            description="Legacy webhook URL",
        )
    if "webhook_secret" in changes:
        value = str(changes["webhook_secret"] or "").strip()
        _upsert(
            db,
            KEY_WEBHOOK_SECRET,
            encrypt_secret(value) if value else None,
            is_secret=True,
            description="Legacy webhook secret",
        )

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
    """Public shape: channel secrets masked, plus an explicit ``configured`` flag."""

    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "include_wait": config.include_wait,
        "quiet_hours": config.quiet_hours,
        "daily_max": config.daily_max,
        "cooldown_minutes": config.cooldown_minutes,
        "base_url": config.base_url,
        "enabled_at": config.enabled_at,
        "eligible_states": list(config.eligible_states),
        "channels": [serialize_channel(channel) for channel in config.channels],
    }
