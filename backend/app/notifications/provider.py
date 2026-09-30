"""Generic webhook notification provider (M11 / docs/03_MODULES.md).

The webhook is the only outbound channel V1 implements. It is deliberately
small and safe:

* only ``http://`` / ``https://`` URLs are accepted, and cloud metadata
  endpoints are refused outright (SSRF hardening, docs/14_SECURITY_LICENSE.md);
* redirects are never followed — a webhook that answers 302 to an internal
  address must not be chased;
* the request has a hard timeout so a dead endpoint can never stall a worker;
* when a signing secret is configured the raw body is signed with
  ``X-QuantLab-Signature: sha256=<hmac>`` so the receiver can verify authenticity.

Secrets (the URL may embed a token, the signing secret always is one) are never
logged: errors are sanitised before they bubble up.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "NotificationConfigError",
    "NotificationError",
    "WebhookNotificationProvider",
    "mask_webhook_url",
    "validate_smtp_host",
    "validate_webhook_url",
]

DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_DETAIL_CHARS = 300

# Cloud instance metadata services. A notification URL must never point here:
# the webhook URL is user-supplied, so it is also an SSRF surface.
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata.goog"}
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class NotificationConfigError(ValueError):
    """Raised when a configured notification endpoint is not acceptable."""


class NotificationError(RuntimeError):
    """Raised when a notification could not be delivered."""


def _ip_is_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Link-local covers 169.254.0.0/16 (cloud metadata) and fe80::/10. Private
    # and loopback addresses are deliberately allowed: a NAS user commonly
    # notifies a service on their own LAN.
    return ip.is_link_local


def validate_webhook_url(url: str) -> str:
    """Return the cleaned URL or raise :class:`NotificationConfigError`.

    Plain ``http://`` is allowed (a NAS user may legitimately notify a LAN
    service), and so are private addresses, but non-HTTP schemes, cloud
    metadata hosts and link-local targets are refused. The host is also
    resolved so decimal/octal/IPv6 spellings of the metadata address cannot
    slip past the name blocklist.
    """

    text = (url or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"}:
        raise NotificationConfigError("webhook url must start with http:// or https://")
    host = parsed.hostname
    if not host:
        raise NotificationConfigError("webhook url is missing a host")
    if host.lower() in _BLOCKED_HOSTS:
        raise NotificationConfigError("webhook url points at a cloud metadata endpoint")
    _reject_link_local(host)
    return text


def _reject_link_local(host: str) -> None:
    """Resolve ``host`` (if possible) and refuse link-local addresses.

    Unresolvable hosts are allowed through: the URL may only become resolvable
    later, and the send-time request still cannot reach a link-local target
    without a name resolving to one.
    """

    candidates: list[str] = []
    try:
        ipaddress.ip_address(host.strip("[]"))
        candidates.append(host.strip("[]"))
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return
        candidates = [info[4][0] for info in infos]

    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate.split("%")[0])
        except ValueError:
            continue
        if _ip_is_forbidden(ip):
            raise NotificationConfigError("webhook url resolves to a link-local address")


def validate_smtp_host(host: str) -> str:
    """Validate an SMTP host the same way as an outbound webhook target.

    An SMTP target is user-supplied egress too, so reject cloud metadata hosts
    and link-local addresses (private/LAN relays stay allowed, as with webhooks).
    """

    text = (host or "").strip()
    if not text:
        raise NotificationConfigError("smtp host is required")
    if text.lower() in _BLOCKED_HOSTS:
        raise NotificationConfigError("smtp host points at a cloud metadata endpoint")
    _reject_link_local(text)
    return text


def mask_webhook_url(url: str) -> str:
    """Show only scheme + host; a URL may embed a token in its path."""

    if not url:
        return ""
    parsed = urlsplit(url)
    if not parsed.hostname:
        return "********"
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}/…"


def _sanitize(text: object) -> str:
    # The webhook URL is a secret (it may embed a token); never let it leak
    # through an exception message into the audit log.
    cleaned = _URL_RE.sub("http(s)://<redacted>", str(text))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > _MAX_DETAIL_CHARS:
        cleaned = cleaned[:_MAX_DETAIL_CHARS] + "…"
    return cleaned


class WebhookNotificationProvider:
    """POSTs a JSON payload to a user-configured webhook."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        secret: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.url = validate_webhook_url(url)
        if not self.url:
            raise NotificationConfigError("webhook url is not configured")
        self.secret = (secret or "").strip() or None
        self.timeout = timeout

    def build_body(self, title: str, body: str, meta: dict[str, Any] | None = None) -> bytes:
        """Serialise the payload exactly as it will be signed and sent."""

        payload: dict[str, Any] = {"title": title, "body": body}
        if meta:
            payload.update(meta)
        return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

    def _headers(self, raw: bytes) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            digest = hmac.new(self.secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
            headers["X-QuantLab-Signature"] = f"sha256={digest}"
        return headers

    def send(self, title: str, body: str, *, meta: dict[str, Any] | None = None) -> bool:
        """Deliver one notification. Returns ``True`` on a 2xx response.

        Any transport failure, non-2xx status or timeout raises
        :class:`NotificationError` with a sanitised message so the caller can
        record why the delivery failed without leaking the URL/secret.
        """

        raw = self.build_body(title, body, meta)
        try:
            response = httpx.post(
                self.url,
                content=raw,
                headers=self._headers(raw),
                timeout=self.timeout,
                follow_redirects=False,
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the worker
            raise NotificationError(_sanitize(f"{type(exc).__name__}: {exc}")) from exc

        if 200 <= response.status_code < 300:
            return True
        raise NotificationError(f"webhook responded HTTP {response.status_code}")
