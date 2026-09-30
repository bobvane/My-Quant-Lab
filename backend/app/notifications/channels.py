"""Notification channels: model, storage (encrypted secrets) and dispatch.

A channel is one delivery destination. V1 shipped a single webhook; P1 adds
Feishu, Telegram, PushPlus and Email (docs/03 M11). Channels are stored as a
JSON list in ``system_settings``; secret fields inside each channel are
encrypted at rest and only ever exposed masked, exactly like the old webhook.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.infrastructure.secrets import decrypt_secret, encrypt_secret, mask_secret
from app.notifications.provider import (
    NotificationConfigError,
    WebhookNotificationProvider,
    mask_webhook_url,
)
from app.notifications.providers import (
    EmailProvider,
    FeishuProvider,
    PushPlusProvider,
    TelegramProvider,
)

__all__ = [
    "CHANNEL_TYPES",
    "NotificationChannel",
    "channel_from_storage",
    "channel_to_storage",
    "build_provider",
    "normalize_channel",
    "serialize_channel",
]

CHANNEL_TYPES = ("webhook", "feishu", "telegram", "pushplus", "email")

# Per-type field defaults. Secret fields are encrypted at rest.
DEFAULTS: dict[str, dict[str, Any]] = {
    "webhook": {"url": "", "secret": ""},
    "feishu": {"url": "", "secret": ""},
    "telegram": {"bot_token": "", "chat_id": ""},
    "pushplus": {"token": "", "topic": ""},
    "email": {
        "host": "",
        "port": 587,
        "username": "",
        "password": "",
        "from_address": "",
        "to_address": "",
        "use_tls": True,
        "use_ssl": False,
    },
}
SECRET_FIELDS: dict[str, set[str]] = {
    "webhook": {"url", "secret"},
    "feishu": {"url", "secret"},
    "telegram": {"bot_token"},
    "pushplus": {"token"},
    "email": {"password"},
}
_ENC_PREFIX = "enc:"
_MAX_FIELD_LEN = 4096


@dataclass(frozen=True)
class NotificationChannel:
    id: str
    type: str
    enabled: bool
    config: dict[str, Any]

    @property
    def secret_fields(self) -> set[str]:
        return SECRET_FIELDS[self.type]


def _encrypt(value: str) -> str:
    return _ENC_PREFIX + encrypt_secret(value)


def _decrypt(value: str) -> str:
    if value.startswith(_ENC_PREFIX):
        try:
            return decrypt_secret(value[len(_ENC_PREFIX) :])
        except Exception:  # noqa: BLE001 - corrupted ciphertext, treat as empty
            return ""
    return value


def build_provider(channel: NotificationChannel):
    """Instantiate the provider for a channel (validates its configuration)."""

    cfg = channel.config
    kind = channel.type
    if kind == "webhook":
        return WebhookNotificationProvider(str(cfg.get("url") or ""), str(cfg.get("secret") or ""))
    if kind == "feishu":
        return FeishuProvider(str(cfg.get("url") or ""), str(cfg.get("secret") or ""))
    if kind == "telegram":
        return TelegramProvider(str(cfg.get("bot_token") or ""), str(cfg.get("chat_id") or ""))
    if kind == "pushplus":
        return PushPlusProvider(str(cfg.get("token") or ""), str(cfg.get("topic") or ""))
    if kind == "email":
        return EmailProvider(
            str(cfg.get("host") or ""),
            str(cfg.get("to_address") or ""),
            port=int(cfg.get("port") or 587),
            username=str(cfg.get("username") or ""),
            password=str(cfg.get("password") or ""),
            from_address=str(cfg.get("from_address") or ""),
            use_tls=bool(cfg.get("use_tls", True)),
            use_ssl=bool(cfg.get("use_ssl", False)),
        )
    raise NotificationConfigError(f"unknown channel type '{kind}'")


def normalize_channel(
    raw: dict[str, Any], *, previous: NotificationChannel | None = None
) -> NotificationChannel:
    """Validate and coerce one incoming channel definition.

    Secret fields that are omitted keep the previously stored value; send an
    empty string to clear one. Only enabled channels must be complete.
    """

    kind = str(raw.get("type") or "").strip()
    if kind not in DEFAULTS:
        raise NotificationConfigError(f"unknown channel type '{kind}'")
    channel_id = str(raw.get("id") or "").strip() or f"{kind}-{uuid.uuid4().hex[:6]}"
    enabled = bool(raw.get("enabled", True))

    config = dict(DEFAULTS[kind])
    if previous is not None and previous.type == kind:
        config.update(previous.config)
    for field in DEFAULTS[kind]:
        if field not in raw:
            continue
        value = raw[field]
        if field in SECRET_FIELDS[kind] and (value is None or value == ""):
            # None -> keep stored; "" -> clear.
            if value is None:
                continue
            config[field] = ""
            continue
        config[field] = value

    if kind == "email":
        try:
            port = int(config.get("port") or 587)
        except (TypeError, ValueError) as exc:
            raise NotificationConfigError("email port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise NotificationConfigError("email port must be between 1 and 65535")
        config["port"] = port
    for field, value in config.items():
        if isinstance(value, str) and len(value) > _MAX_FIELD_LEN:
            raise NotificationConfigError(f"{field} is too long")

    channel = NotificationChannel(id=channel_id, type=kind, enabled=enabled, config=config)
    if enabled:
        build_provider(channel)  # raises NotificationConfigError on bad config
    return channel


def channel_to_storage(channel: NotificationChannel) -> dict[str, Any]:
    """Serialise for the settings table with secret fields encrypted."""

    stored: dict[str, Any] = {"id": channel.id, "type": channel.type, "enabled": channel.enabled}
    for field, value in channel.config.items():
        if field in channel.secret_fields and value:
            stored[field] = _encrypt(str(value))
        else:
            stored[field] = value
    return stored


def channel_from_storage(raw: dict[str, Any]) -> NotificationChannel:
    kind = str(raw.get("type") or "")
    if kind not in DEFAULTS:
        raise NotificationConfigError(f"stored channel has unknown type '{kind}'")
    config = dict(DEFAULTS[kind])
    for field in DEFAULTS[kind]:
        if field not in raw:
            continue
        value = raw[field]
        config[field] = _decrypt(value) if isinstance(value, str) else value
    return NotificationChannel(
        id=str(raw.get("id") or f"{kind}-unknown"),
        type=kind,
        enabled=bool(raw.get("enabled", False)),
        config=config,
    )


def serialize_channel(channel: NotificationChannel) -> dict[str, Any]:
    """Public shape: secrets only ever appear masked + an ``is_set`` flag."""

    out: dict[str, Any] = {"id": channel.id, "type": channel.type, "enabled": channel.enabled}
    for field, default in DEFAULTS[channel.type].items():
        value = channel.config.get(field, default)
        if field in channel.secret_fields:
            text = str(value or "")
            out[f"{field}_set"] = bool(text)
            if field == "url":
                out[f"{field}_masked"] = mask_webhook_url(text)
            else:
                out[f"{field}_masked"] = mask_secret(text)
        else:
            out[field] = value
    return out
