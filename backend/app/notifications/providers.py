"""Outbound notification channels beyond the generic webhook (docs/03 M11 P1).

Each class is a small, self-contained adapter that translates the shared
``(title, body, meta)`` message into one provider's HTTP/SMTP shape. They all:

* validate their configuration up front and raise ``NotificationConfigError``;
* raise ``NotificationError`` (with a sanitised message that never contains the
  URL/token/password) when delivery fails;
* use a fixed timeout and never follow redirects.

Email rides on the standard library (``smtplib``) so no extra dependency is
introduced. Feishu/Telegram/PushPlus reuse ``httpx`` like the webhook.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Any

import httpx

from app.notifications.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    NotificationConfigError,
    NotificationError,
    _sanitize,
    validate_smtp_host,
    validate_webhook_url,
)

logger = logging.getLogger(__name__)

__all__ = [
    "EmailProvider",
    "FeishuProvider",
    "PushPlusProvider",
    "TelegramProvider",
]


def _require(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NotificationConfigError(f"{field} is required")
    return text


def _post_json(url: str, payload: dict[str, Any], *, timeout: float) -> None:
    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=timeout,
            follow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash the worker
        raise NotificationError(_sanitize(f"{type(exc).__name__}: {exc}")) from exc
    if not 200 <= response.status_code < 300:
        raise NotificationError(f"channel responded HTTP {response.status_code}")


def _compose(title: str, body: str, meta: dict[str, Any] | None) -> str:
    text = f"{title}\n{body}"
    link = (meta or {}).get("link")
    if link:
        text = f"{text}\n{link}"
    return text


class FeishuProvider:
    """Feishu / Lark custom bot webhook (text message, optional signed)."""

    name = "feishu"

    def __init__(
        self, url: str, secret: str | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.url = validate_webhook_url(url)
        self.secret = (secret or "").strip() or None
        self.timeout = timeout

    def _sign(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def send(self, title: str, body: str, *, meta: dict[str, Any] | None = None) -> bool:
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": _compose(title, body, meta)},
        }
        if self.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        _post_json(self.url, payload, timeout=self.timeout)
        return True


class TelegramProvider:
    """Telegram Bot API sendMessage."""

    name = "telegram"
    BASE_URL = "https://api.telegram.org"

    def __init__(
        self, bot_token: str, chat_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.bot_token = _require(bot_token, "bot_token")
        self.chat_id = _require(chat_id, "chat_id")
        self.timeout = timeout

    def send(self, title: str, body: str, *, meta: dict[str, Any] | None = None) -> bool:
        url = f"{self.BASE_URL}/bot{self.bot_token}/sendMessage"
        _post_json(
            url,
            {"chat_id": self.chat_id, "text": _compose(title, body, meta)},
            timeout=self.timeout,
        )
        return True


class PushPlusProvider:
    """PushPlus (pushplus.plus) WeChat push."""

    name = "pushplus"
    ENDPOINT = "https://www.pushplus.plus/send"

    def __init__(
        self, token: str, topic: str | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.token = _require(token, "token")
        self.topic = (topic or "").strip()
        self.timeout = timeout

    def send(self, title: str, body: str, *, meta: dict[str, Any] | None = None) -> bool:
        payload: dict[str, Any] = {
            "token": self.token,
            "title": title,
            "content": _compose("", body, meta).strip(),
            "template": "txt",
        }
        if self.topic:
            payload["topic"] = self.topic
        _post_json(self.ENDPOINT, payload, timeout=self.timeout)
        return True


class EmailProvider:
    """Plain SMTP email via the standard library."""

    name = "email"

    def __init__(
        self,
        host: str,
        to_address: str,
        *,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        from_address: str | None = None,
        use_tls: bool = True,
        use_ssl: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.host = validate_smtp_host(host)
        self.port = int(port)
        if not 1 <= self.port <= 65535:
            raise NotificationConfigError("port must be between 1 and 65535")
        self.username = (username or "").strip()
        self.password = password or ""
        self.from_address = (from_address or self.username).strip()
        if not self.from_address:
            raise NotificationConfigError("from_address (or username) is required")
        self.to_address = _require(to_address, "to_address")
        self.use_tls = bool(use_tls) and not use_ssl
        self.use_ssl = bool(use_ssl)
        if self.username and not (self.use_tls or self.use_ssl):
            raise NotificationConfigError(
                "refusing to send SMTP credentials over an unencrypted connection"
            )
        self.timeout = timeout

    def _recipients(self) -> list[str]:
        return [part.strip() for part in self.to_address.split(",") if part.strip()]

    def send(self, title: str, body: str, *, meta: dict[str, Any] | None = None) -> bool:
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.from_address
        message["To"] = ", ".join(self._recipients())
        message.set_content(_compose("", body, meta).strip())

        # Verify the server certificate: a MITM must not be able to harvest the
        # SMTP credentials. smtplib's default context disables verification.
        context = ssl.create_default_context()
        try:
            if self.use_ssl:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=context
                )
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with client:
                if self.use_tls:
                    client.starttls(context=context)
                if self.username:
                    client.login(self.username, self.password)
                client.send_message(message)
        except Exception as exc:  # noqa: BLE001 - report, never crash the worker
            raise NotificationError(_sanitize(f"{type(exc).__name__}: {exc}")) from exc
        return True
