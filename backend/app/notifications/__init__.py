"""Notification layer (M11).

V1 ships one outbound channel: a generic webhook. Adding Feishu/Telegram/Email
later means adding another :class:`app.domain.protocols.NotificationProvider`
implementation without touching the signal engine.
"""

from app.notifications.config import (
    NotificationConfig,
    get_notification_config,
    serialize_notification_config,
    update_notification_config,
)
from app.notifications.provider import (
    NotificationConfigError,
    NotificationError,
    WebhookNotificationProvider,
)
from app.notifications.service import (
    build_signal_payload,
    notify_pending_signals,
    send_test_notification,
)

__all__ = [
    "NotificationConfig",
    "NotificationConfigError",
    "NotificationError",
    "WebhookNotificationProvider",
    "build_signal_payload",
    "get_notification_config",
    "notify_pending_signals",
    "send_test_notification",
    "serialize_notification_config",
    "update_notification_config",
]
