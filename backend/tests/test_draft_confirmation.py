"""The human confirmation step for a strategy draft (v2.4.0 Step 1).

The gate is deliberately narrow. A confirmation is *evidence*: exactly one
append-only audit event, readable next to the draft it is about, and never a
strategy version -- no AI-side endpoint may set ``is_current``, so recording
"confirmed" cannot put anything into the signal path. The 404/422 edges and the
bearer token in front of the write are pinned down here too.
"""

from __future__ import annotations

import pytest
from research_payloads import draft_payload, hypothesis_payload, make_provider, run_research
from sqlalchemy import select

from app.core.config import settings
from app.domain.models import AuditLog, StrategyDraft, StrategyVersion

CONFIRM_EVENTS = (
    "strategy_draft_confirmed",
    "strategy_draft_rejected",
    "strategy_draft_needs_revision",
)


@pytest.fixture()
def provider(db_session):
    return make_provider(db_session)


@pytest.fixture()
def drafted_run(db_session, provider) -> StrategyDraft:
    """A stored draft, produced by the real research pipeline with a scripted model."""

    run, _router = run_research(db_session, provider, [hypothesis_payload(), draft_payload()])
    assert run.status == "completed"
    return db_session.scalars(select(StrategyDraft)).one()


def _confirm(client, draft_id: int, decision: str, note: str | None = None):
    body: dict[str, object] = {"decision": decision}
    if note is not None:
        body["note"] = note
    return client.post(f"/api/v1/ai/strategy/drafts/{draft_id}/confirmations", json=body)


def _audit_rows(db_session) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.event_type.in_(CONFIRM_EVENTS)).order_by(AuditLog.id)
        )
    )


def _current_versions(db_session) -> list[StrategyVersion]:
    return list(
        db_session.scalars(select(StrategyVersion).where(StrategyVersion.is_current.is_(True)))
    )


def test_a_confirmation_is_evidence_and_never_a_strategy_version(client, db_session, drafted_run):
    response = _confirm(client, drafted_run.id, "confirmed", "looks tradeable")

    assert response.status_code == 201
    body = response.json()
    assert body["draft_id"] == drafted_run.id
    assert body["run_id"] == drafted_run.run_id
    assert body["decision"] == "confirmed"
    assert body["label"] == "已确认"
    assert body["strategy_version_created"] is False
    assert body["compiled_strategy_version_id"] is None
    assert body["confirmation"]["decision"] == "confirmed"
    assert body["confirmation"]["decided_by"] == "operator"
    assert body["confirmation"]["note"] == "looks tradeable"
    assert body["confirmation"]["is_human_decision"] is True
    assert isinstance(body["confirmation"]["audit_id"], int)

    rows = _audit_rows(db_session)
    assert len(rows) == 1
    event = rows[0]
    assert event.event_type == "strategy_draft_confirmed"
    assert event.entity_type == "strategy_draft"
    assert event.entity_id == str(drafted_run.id)
    assert event.action == "confirm"
    assert event.actor == "operator"
    assert event.payload_json is not None
    assert event.payload_json["is_human_decision"] is True
    assert event.payload_json["strategy_version_created"] is False

    # The decision documented what a human thinks; it produced nothing runnable.
    assert db_session.scalars(select(StrategyVersion)).all() == []
    assert db_session.get(StrategyDraft, drafted_run.id).executable is False


def test_the_run_payload_carries_the_latest_confirmation(client, drafted_run):
    before = client.get(f"/api/v1/ai/research/{drafted_run.run_id}")
    assert before.status_code == 200
    assert before.json()["draft"]["confirmation"] is None

    _confirm(client, drafted_run.id, "needs_revision", "tighten the exit rule")

    payload = client.get(f"/api/v1/ai/research/{drafted_run.run_id}").json()
    confirmation = payload["draft"]["confirmation"]
    assert confirmation["decision"] == "needs_revision"
    assert confirmation["label"] == "需修改"
    assert confirmation["note"] == "tighten the exit rule"
    assert confirmation["decided_by"] == "operator"
    assert confirmation["decided_at"]


def test_decisions_accumulate_and_the_latest_one_wins(client, db_session, drafted_run):
    _confirm(client, drafted_run.id, "needs_revision")
    _confirm(client, drafted_run.id, "confirmed", "second look")
    _confirm(client, drafted_run.id, "rejected", "too correlated with the benchmark")

    rows = _audit_rows(db_session)
    assert [row.event_type for row in rows] == [
        "strategy_draft_needs_revision",
        "strategy_draft_confirmed",
        "strategy_draft_rejected",
    ]
    assert [row.action for row in rows] == ["request_changes", "confirm", "reject"]

    payload = client.get(f"/api/v1/ai/research/{drafted_run.run_id}").json()
    assert payload["draft"]["confirmation"]["decision"] == "rejected"
    assert payload["draft"]["confirmation"]["note"] == "too correlated with the benchmark"


def test_unknown_draft_and_bad_payloads_are_refused(client, db_session, drafted_run):
    missing = _confirm(client, 999_999, "confirmed")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "draft_not_found"

    assert _confirm(client, drafted_run.id, "maybe").status_code == 422
    assert _confirm(client, drafted_run.id, "confirmed", "x" * 2001).status_code == 422

    extra = client.post(
        f"/api/v1/ai/strategy/drafts/{drafted_run.id}/confirmations",
        json={"decision": "confirmed", "strategy_version_id": 1},
    )
    assert extra.status_code == 422

    # A refused request must not leave a decision behind.
    assert _audit_rows(db_session) == []
    payload = client.get(f"/api/v1/ai/research/{drafted_run.run_id}").json()
    assert payload["draft"]["confirmation"] is None


def test_nothing_an_ai_endpoint_does_makes_a_version_current(client, db_session, drafted_run):
    """Confirmed or not, the AI path cannot reach the signal scanner (ADR-171 intact)."""

    assert _current_versions(db_session) == []

    compiled = client.post(
        f"/api/v1/ai/strategy/drafts/{drafted_run.id}/compile",
        json={"strategy_id": _new_strategy(client)},
    )
    assert compiled.status_code in (201, 422)

    _confirm(client, drafted_run.id, "confirmed", "ship the research version")

    assert _current_versions(db_session) == []
    if compiled.status_code == 201:
        version = db_session.get(StrategyVersion, compiled.json()["strategy_version_id"])
        assert version is not None
        assert version.validation_status == "valid"
        # The compiler's product is deliberately not live: a human still has to
        # activate it, and only the ADR-171 gate lets a valid version through.
        assert version.is_current is False


def _new_strategy(client) -> int:
    created = client.post("/api/v1/strategies", json={"name": "Confirmed draft target"})
    assert created.status_code == 201
    return int(created.json()["id"])


def test_the_confirmation_endpoint_is_behind_the_bearer_token(client, monkeypatch, drafted_run):
    token = "s3cret-token-123"
    monkeypatch.setattr(settings, "api_auth_token", token)
    url = f"/api/v1/ai/strategy/drafts/{drafted_run.id}/confirmations"
    body = {"decision": "confirmed"}

    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(url, json=body, headers={"Authorization": token}).status_code == 401
    allowed = client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    assert allowed.status_code == 201
