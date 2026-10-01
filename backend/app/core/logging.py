"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from app.core.config import settings

_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*['\"]?([^\s'\",}]+)"
)
# Also catches JSON-style "key":"value" with quotes between keyword and colon.
_SECRET_JSON_PATTERN = re.compile(
    r"(?i)\"(api[_-]?key|token|secret|password|authorization)\"\s*:\s*\"[^\"]+\""
)


def _redact(text: str) -> str:
    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***", text)
    return _SECRET_JSON_PATTERN.sub(r'"\1": "***"', text)


class RedactingFilter(logging.Filter):
    """Remove secret-looking values from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        message = _redact(record.getMessage())
        if isinstance(record.args, (tuple, dict)) and record.args:
            record.msg = message
            record.args = ()
        else:
            record.msg = message
        return True


class JsonFormatter(logging.Formatter):
    """Minimal single-line JSON formatter (no external dependency)."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Install handlers once at process start."""

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)

    logging.getLogger("uvicorn.access").addFilter(RedactingFilter())
    # Outbound request URLs may embed credentials (e.g. the Telegram bot token),
    # so never let httpx log them even when LOG_LEVEL=DEBUG is misconfigured.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))


configure_logging()
