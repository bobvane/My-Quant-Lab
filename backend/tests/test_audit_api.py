"""Audit log query endpoints (docs/12)."""

from __future__ import annotations

from app.data.strategy_service import record_audit


def test_audit_logs_and_entity_filter(client, db_session) -> None:
    record_audit(
        db_session,
        event_type="strategy_created",
        entity_type="strategy",
        entity_id="42",
        action="create",
        payload={"name": "A"},
    )
    record_audit(
        db_session,
        event_type="signal_status_changed",
        entity_type="signal",
        entity_id="7",
        action="acknowledge",
        payload={},
    )
    db_session.commit()

    all_events = client.get("/api/v1/audit/logs").json()
    assert all_events["total"] == 2

    strategy_events = client.get("/api/v1/audit/logs/entity/strategy/42").json()
    assert strategy_events["total"] == 1
    assert strategy_events["events"][0]["event_type"] == "strategy_created"

    empty = client.get("/api/v1/audit/logs/entity/strategy/999").json()
    assert empty["total"] == 0


def test_total_counts_the_ledger_not_the_page(client, db_session) -> None:
    for entity_id in ("1", "2", "3"):
        record_audit(
            db_session,
            event_type="strategy_version_created",
            entity_type="strategy",
            entity_id=entity_id,
            action="create",
            payload={},
        )
    db_session.commit()

    page = client.get("/api/v1/audit/logs", params={"limit": 2}).json()
    assert page["total"] == 3, "total must count the ledger, not the page it came back on"
    assert len(page["events"]) == 2

    rest = client.get("/api/v1/audit/logs", params={"limit": 2, "offset": 2}).json()
    assert rest["total"] == 3
    assert len(rest["events"]) == 1


def test_the_entity_total_only_counts_that_entity(client, db_session) -> None:
    for entity_id in ("42", "42", "43"):
        record_audit(
            db_session,
            event_type="signal_status_changed",
            entity_type="signal",
            entity_id=entity_id,
            action="acknowledge",
            payload={},
        )
    db_session.commit()

    body = client.get("/api/v1/audit/logs/entity/signal/42", params={"limit": 1}).json()

    assert body["total"] == 2
    assert len(body["events"]) == 1


def test_every_event_names_its_actor(client, db_session) -> None:
    record_audit(
        db_session,
        event_type="strategy_retired",
        entity_type="strategy",
        entity_id="1",
        action="retire",
        payload={},
        actor="user",
    )
    db_session.commit()

    event = client.get("/api/v1/audit/logs").json()["events"][0]

    assert event["actor"] == "user"


def test_the_duplicate_audit_route_is_gone(client) -> None:
    """`/settings/audit` served the same ledger without `actor` (ADR-070)."""

    assert client.get("/api/v1/settings/audit").status_code == 404
