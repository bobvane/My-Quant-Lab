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
    """Remove secret-looking values from every log record.

    Normal records are rendered here and redacted as a whole: ``record.msg``
    becomes the finished message and ``record.args`` is cleared, so the handler
    must never render the record a second time.

    ``uvicorn.access`` is special.  Its formatter unpacks ``record.args`` as a
    five-tuple ``(client, method, full_path, http_version, status)``, so
    clearing the args makes ``AccessFormatter.formatMessage`` raise
    ``ValueError: not enough values to unpack (expected 5, got 0)`` and every
    access line is lost.  That tuple is kept intact and redacted element by
    element instead, which still catches credentials smuggled into a query
    string (``?token=...``).
    """

    #: Logger names whose records are redacted argument by argument.
    structured_arg_loggers = frozenset({"uvicorn.access"})

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if record.name in self.structured_arg_loggers and isinstance(record.args, tuple):
            record.args = tuple(
                _redact(item) if isinstance(item, str) else item for item in record.args
            )
            return True
        record.msg = _redact(record.getMessage())
        record.args = ()
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

    # Attached to uvicorn's access logger as well.  The filter redacts that
    # logger's arguments in place instead of clearing them, so uvicorn's own
    # AccessFormatter still finds its five positional args.
    logging.getLogger("uvicorn.access").addFilter(RedactingFilter())
    # Outbound request URLs may embed credentials (e.g. the Telegram bot token),
    # so never let httpx log them even when LOG_LEVEL=DEBUG is misconfigured.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))


configure_logging()
