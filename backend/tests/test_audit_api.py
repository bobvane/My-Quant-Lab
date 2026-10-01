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
