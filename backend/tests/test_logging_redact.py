"""Log redaction helpers (docs/14 §1, review L-3)."""

from __future__ import annotations

import logging
import sys

import pytest

from app.core.logging import RedactingFilter, _redact


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


def test_filter_keeps_resolved_message_from_double_formatting(capsys) -> None:
    """A redacted record must not be rendered a second time.

    The filter stores the already-rendered message in ``record.msg``, so the
    handler must not re-apply ``record.args`` to it: that would print a literal
    ``%s`` and let a bare ``%`` in a message reach the ``%``-formatter.
    """

    logger = logging.getLogger("mql-test-double-format")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    try:
        logger.info("using token=%s now", "ghp_secret")
        logger.error("scanned 12 symbols, 100% done")
    finally:
        logger.handlers = []

    out = capsys.readouterr().out
    assert "using token=*** now" in out
    assert "ghp_secret" not in out
    assert "%s" not in out
    assert "scanned 12 symbols, 100% done" in out


def test_uvicorn_access_record_keeps_a_five_tuple_of_args() -> None:
    """uvicorn's AccessFormatter unpacks record.args as a five-tuple.

    Clearing the args (the normal redaction path) makes it raise
    ``ValueError: not enough values to unpack (expected 5, got 0)`` and drops
    every access line, so the filter must redact the args in place instead.
    A credential smuggled into the query string must still be removed.
    """

    uvicorn_logging = pytest.importorskip("uvicorn.logging")

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:52000", "POST", "/api/v1/research/ensemble?token=abc", "1.1", 200),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert isinstance(record.args, tuple)
    assert len(record.args) == 5

    formatter = uvicorn_logging.AccessFormatter(
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    rendered = formatter.format(record)
    assert "127.0.0.1:52000" in rendered
    assert "POST /api/v1/research/ensemble?token=*** HTTP/1.1" in rendered
    assert "200" in rendered
    assert "abc" not in rendered


def test_filter_does_not_crash_the_real_access_formatter_path() -> None:
    """End to end guard: the old behaviour raised inside ``logging.emit``.

    ``logging.Handler.handle`` runs the filters and then formats, exactly like
    a live uvicorn serve; reproducing that order is what makes this a
    regression test rather than a property test.
    """

    uvicorn_logging = pytest.importorskip("uvicorn.logging")

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)
            self.format(record)  # raises when record.args was stripped

    collector = _Collector()
    collector.setFormatter(uvicorn_logging.AccessFormatter("%(request_line)s %(status_code)s"))
    collector.addFilter(RedactingFilter())
    access_logger = logging.getLogger("uvicorn.access")
    original_handlers = access_logger.handlers
    original_propagate = access_logger.propagate
    access_logger.handlers = [collector]
    access_logger.propagate = False
    try:
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:52000",
            "GET",
            "/api/v1/healthz?api_key=sk-live-secret",
            "1.1",
            200,
        )
    except ValueError as exc:  # pragma: no cover - only on regression
        pytest.fail(f"access log rendering broke: {exc}")
    finally:
        access_logger.handlers = original_handlers
        access_logger.propagate = original_propagate

    assert len(records) == 1
    message = collector.format(records[0])
    assert "GET /api/v1/healthz?api_key=*** HTTP/1.1" in message
    assert "sk-live-secret" not in message
