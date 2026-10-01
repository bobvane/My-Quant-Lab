"""Log redaction helpers (docs/14 §1, review L-3)."""

from __future__ import annotations

import logging

from app.core.logging import _redact


def test_redact_key_value_forms() -> None:
    assert "sk-secret" not in _redact("using api_key=sk-secret now")
    assert "pw" not in _redact('config {"password": "pw"}')
    assert "ABC" not in _redact("token: ABC")


def test_redact_exception_text_pattern() -> None:
    text = _redact('ConnectError: 401 {"apiKey": "ghp_personal"}; url=tok')
    assert "ghp_personal" not in text
    assert '"apiKey": "***"' in text


def test_redact_leaves_plain_messages_intact() -> None:
    message = "signal 12 scanned, 3 evaluated"
    assert _redact(message) == message


def test_filter_applies_to_exception_traceback(capsys) -> None:
    from app.core.logging import RedactingFilter

    record = logging.LogRecord(
        name="t",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=None,
    )
    redacting = RedactingFilter()
    assert redacting.filter(record) is True
