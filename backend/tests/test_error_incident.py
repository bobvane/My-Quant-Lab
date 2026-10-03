"""A 500 must leave a trail an operator can follow (ADR-082).

The DEMO-AAPL bug reached production as ``500 internal server error`` with no way to
tell which request it was: the traceback was in the container log, the user had a
response with nothing in common with it. Every unhandled error now carries a short
incident id in the response *and* in the log line, so "it said incident 3f9a1c02" is
enough to find the cause.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app, new_incident_id
from app.core.config import settings
from app.core.db import get_db

INCIDENT = re.compile(r"incident ([0-9a-f]{8})")


def _boom() -> None:
    raise RuntimeError("the bar table turned to sand")


def _app_that_raises(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.add_api_route("/__boom", _boom, methods=["GET"], include_in_schema=False)
    return app


@pytest.fixture()
def boom(db_session):
    with TestClient(_app_that_raises(db_session), raise_server_exceptions=False) as client:
        yield client


def test_an_unhandled_error_names_an_incident_the_log_also_carries(boom, caplog) -> None:
    with caplog.at_level("ERROR", logger="app.api.main"):
        response = boom.get("/__boom")

    assert response.status_code == 500, response.text
    body = response.json()["error"]
    match = INCIDENT.search(body["message"])
    assert match, f"the message must name the incident: {body['message']!r}"
    incident = match.group(1)

    assert body["details"]["incident"] == incident
    # Also as a header, so a proxy log or the browser Network tab keeps it too.
    assert response.headers["X-Incident-Id"] == incident
    assert f"(incident {incident})" in caplog.text, "the log line must name the same incident"
    assert "unhandled error on /__boom" in caplog.text


def test_the_incident_differs_per_error(boom) -> None:
    first = INCIDENT.search(boom.get("/__boom").json()["error"]["message"]).group(1)
    second = INCIDENT.search(boom.get("/__boom").json()["error"]["message"]).group(1)
    assert first != second, "a reused id would point at somebody else's traceback"


def test_outside_production_the_cause_is_returned_with_the_incident(boom) -> None:
    details = boom.get("/__boom").json()["error"]["details"]
    assert details["path"] == "/__boom"
    assert "RuntimeError" in details["exception"]
    assert "the bar table turned to sand" in details["exception"]


def test_production_hides_the_cause_but_keeps_the_incident(boom, monkeypatch) -> None:
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True), raising=False)
    body = boom.get("/__boom").json()["error"]

    assert "exception" not in body["details"], "production must not leak the cause"
    assert "RuntimeError" not in body["message"]
    assert INCIDENT.search(body["message"]), "hiding the cause must not hide the trail"


def test_an_incident_id_is_readable_and_short() -> None:
    ids = {new_incident_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(re.fullmatch(r"[0-9a-f]{8}", value) for value in ids)
