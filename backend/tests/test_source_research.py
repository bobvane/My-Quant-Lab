"""Research runs that read a source the platform fetched itself (Step 3.6).

Before v2.1.0 the research endpoint only accepted material the caller already
had. Now a ``url``/``pdf`` source may name a ``uri`` (the platform fetches and
parses it) or a ``snapshot_id`` (it was already observed). Two rules matter most
here and both are tested from the outside:

* a source refused on policy grounds refuses the whole run — a question answered
  from the sources that happened to be reachable is a different answer;
* a source that cannot be fetched or read fails the run, and neither failure is
  dressed up as an AI rejection.

Nothing here touches the network: the ingester is injected (that injection point
exists precisely so the AI layer stays free of network code), and the model is
answered from a script.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from research_payloads import NOTE, QUESTION, draft_payload, hypothesis_payload, make_provider
from sqlalchemy import func, select

from app.ai import research as service
from app.data import source_snapshot_service as snapshots
from app.domain.models import AIProvider, AIResearchRun, AISourceSnapshot, AITask, ResearchArtifact
from app.sources import ingest
from app.sources.ingest import IngestedMaterial

API = "/api/v1"
PAGE = "https://example.test/paper"


@pytest.fixture
def provider(db_session) -> AIProvider:
    return make_provider(db_session)


def material(**overrides) -> IngestedMaterial:
    """Material as the ingester returns it for a page that was read successfully.

    ``snapshot_id`` is ``None`` by default: a stub ingester writes no observation,
    and ``research_artifacts.snapshot_id`` is a foreign key, so only a test that
    really stores a row may point at one.
    """

    text = overrides.pop("text", NOTE)
    fields = {
        "kind": "url",
        "status": ingest.RETAINED,
        "parse_status": "ok",
        "snapshot_id": None,
        "uri": PAGE,
        "final_uri": PAGE,
        "text": text,
        "chars_read": len(text),
        "retained_chars": len(text),
        "source_hash": hashlib.sha256(b"<html>page</html>").hexdigest(),
        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
        "size_bytes": 18,
        "http_status": 200,
        "content_type": "text/html",
        "parser": "stdlib.html.parser",
        "parser_version": "1.0",
        "robots_ok": True,
        "metadata": {},
        "fetched_at": datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return IngestedMaterial(**fields)


def ingester(returned, calls: list | None = None):
    """An injected ingester that hands back one recipe and records what it saw."""

    def read(item):
        if calls is not None:
            calls.append(item)
        if isinstance(returned, Exception):
            raise returned
        return returned

    return read


def url_input(**overrides) -> service.ResearchInput:
    fields = {"kind": "url", "uri": PAGE, "source_ref": "page"}
    fields.update(overrides)
    return service.ResearchInput(**fields)


def patch_pipeline(monkeypatch, outputs, seen: list | None = None):
    """Answer the pipeline from a script, and pretend one provider is configured."""

    queue = list(outputs)

    def fake_run_task(db, request, **kwargs):
        if seen is not None:
            seen.append(request)
        if not queue:
            raise AssertionError("the pipeline asked for more answers than the script holds")
        return {
            "explanation": queue.pop(0),
            "cached": False,
            "task_id": None,
            "model": "test-model",
            "cost_usd_estimated": 0.0,
        }

    monkeypatch.setattr(service, "run_task", fake_run_task)
    monkeypatch.setattr(
        service, "get_active_providers", lambda db: [("fake-provider", "sk-test", [])]
    )


def run_with(db_session, provider, returned, **overrides):
    calls: list = []
    run = service.start_research(
        db_session,
        question=QUESTION,
        inputs=overrides.pop("inputs", [url_input()]),
        providers=[(provider, "sk-test", [])],
        router_factory=None,
        ingest=ingester(returned, calls),
        **overrides,
    )
    db_session.commit()
    return run, calls


# ------------------------------------------------------------------ the service


def test_a_fetched_source_is_read_through_the_injected_ingester(db_session, provider, monkeypatch):
    patch_pipeline(monkeypatch, [hypothesis_payload("page"), draft_payload("page")])
    run, calls = run_with(db_session, provider, material())

    assert run.status == "completed"
    assert len(calls) == 1  # fetched once, not once per step
    assert calls[0].uri == PAGE

    artifact = db_session.scalars(select(ResearchArtifact)).one()
    assert artifact.snapshot_id is None  # the stub wrote no observation
    assert artifact.parse_status == "ok"
    assert artifact.uri == PAGE
    # The run reports the identity the observation recorded, so the material the
    # model read traces back to the snapshot it came from (docs/27 §8).
    assert artifact.source_hash == hashlib.sha256(b"<html>page</html>").hexdigest()
    assert artifact.text_hash == hashlib.sha256(NOTE.encode()).hexdigest()

    summary = run.sources_json[0]
    assert summary["snapshot_id"] is None
    assert summary["status_code"] == 200
    assert summary["content_type"] == "text/html"
    assert summary["bytes_read"] == 18
    assert summary["chars_read"] == len(NOTE)
    assert summary["parser"] == "stdlib.html.parser"
    assert summary["truncated"] is False
    assert summary["final_uri"] == PAGE


def test_the_fetched_text_reaches_the_model_as_untrusted_material(
    db_session, provider, monkeypatch
):
    seen: list = []
    patch_pipeline(monkeypatch, [hypothesis_payload("page"), draft_payload("page")], seen=seen)
    run, _calls = run_with(db_session, provider, material())

    assert run.status == "completed"
    assert seen, "the pipeline was never asked anything"
    sources = [source for request in seen for source in request.untrusted_sources]
    assert [source.ref for source in sources] == ["page", "page"]
    assert all(source.text == NOTE for source in sources)
    assert all(source.kind == "url" for source in sources)


def test_the_caller_s_own_text_is_never_second_guessed_by_a_fetch(
    db_session, provider, monkeypatch
):
    patch_pipeline(monkeypatch, [hypothesis_payload("page"), draft_payload("page")])
    run, calls = run_with(
        db_session,
        provider,
        RuntimeError("the ingester must not be called when text is given"),
        inputs=[url_input(text=f"{NOTE} 另外，调用方自己也补了一句。")],
    )

    assert run.status == "completed"
    assert calls == []
    assert [warning["kind"] for warning in run.warnings_json] == ["text_preferred"]
    artifact = db_session.scalars(select(ResearchArtifact)).one()
    assert artifact.snapshot_id is None
    assert artifact.parse_status == "ok"


def test_a_source_blocked_on_policy_refuses_the_whole_run(db_session, provider):
    blocked = material(
        status=ingest.BLOCKED,
        code="robots_disallowed",
        message="robots.txt disallows /paper",
        text="",
        parse_status="not_parsed",
    )
    # A refusal is an observation too: it is stored before the run is refused.
    row = snapshots.record_material(db_session, blocked)
    blocked = replace(blocked, snapshot_id=row.id)
    run, _calls = run_with(db_session, provider, blocked)

    assert run.status == "rejected"
    assert run.current_step == "ingest"
    violation = run.violations_json[0]
    assert violation["kind"] == "source_blocked"
    assert violation["code"] == "robots_disallowed"
    assert violation["source_ref"] == "page"
    assert violation["snapshot_id"] == row.id
    # No model was asked, and the refusal kept the source summary empty: a partial
    # reading of the question is not silently stored as an answer.
    assert run.sources_json == []
    assert db_session.scalar(select(func.count()).select_from(AITask)) == 0


def test_a_source_that_cannot_be_fetched_fails_the_run(db_session, provider):
    run, _calls = run_with(
        db_session,
        provider,
        material(
            status=ingest.FETCH_FAILED,
            code="timeout",
            message="the site did not answer in time",
            text="",
            parse_status="not_parsed",
        ),
    )

    assert run.status == "failed"
    assert run.current_step == "ingest"
    assert "did not answer in time" in (run.error_message or "")
    assert db_session.scalar(select(func.count()).select_from(AITask)) == 0


def test_a_source_with_no_readable_text_fails_the_run(db_session, provider):
    # A scanned PDF: the observation is kept honestly, but there is nothing to
    # research, and empty material is never passed off as an empty source.
    run, _calls = run_with(
        db_session,
        provider,
        material(
            kind="pdf",
            status=ingest.RETAINED,
            parse_status="unsupported",
            code="parse_unsupported",
            message="the PDF has no text layer",
            text="",
            retained_chars=0,
        ),
    )

    assert run.status == "failed"
    assert run.current_step == "ingest"
    assert "parse_unsupported" in (run.error_message or "")


def test_a_snapshot_reference_is_handed_to_the_ingester(db_session, provider, monkeypatch):
    patch_pipeline(monkeypatch, [hypothesis_payload("page"), draft_payload("page")])
    row = snapshots.record_material(db_session, material())
    run, calls = run_with(
        db_session,
        provider,
        material(snapshot_id=row.id),
        inputs=[url_input(uri=None, snapshot_id=row.id)],
    )

    assert run.status == "completed"
    assert calls[0].snapshot_id == row.id
    assert db_session.scalars(select(ResearchArtifact)).one().snapshot_id == row.id


def test_a_deployment_that_cannot_fetch_says_so(db_session, provider):
    with pytest.raises(ValueError, match="this deployment cannot fetch a 'url' source"):
        service.start_research(
            db_session,
            question=QUESTION,
            inputs=[url_input()],
            providers=[(provider, "sk-test", [])],
            router_factory=None,
        )
    assert db_session.scalar(select(func.count()).select_from(AIResearchRun)) == 0


# ------------------------------------------------------------------ the endpoint


def test_the_endpoint_reports_a_blocked_source_as_a_refusal(client, db_session, monkeypatch):
    blocked = material(status=ingest.BLOCKED, code="address_not_allowed", text="")
    # Only the network is stubbed: the real ingester stores the refusal and names it.
    monkeypatch.setattr(snapshots, "ingest_url", lambda uri, **kw: replace(blocked, uri=uri))

    response = client.post(
        f"{API}/ai/research",
        json={
            "question": QUESTION,
            "sources": [{"kind": "url", "uri": PAGE, "source_ref": "page"}],
        },
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "source_blocked"
    assert detail["code"] == "address_not_allowed"
    assert detail["source_ref"] == "page"
    assert detail["violations"][0]["kind"] == "source_blocked"

    # The observation and the run are both stored, so the refusal is visible afterwards.
    stored_row = db_session.scalars(select(AISourceSnapshot)).one()
    assert stored_row.status == "blocked"
    assert detail["snapshot_id"] == stored_row.id
    stored = db_session.scalars(select(AIResearchRun)).one()
    assert stored.status == "rejected"
    assert client.get(f"{API}/ai/research/{stored.id}").json()["status"] == "rejected"


def test_the_endpoint_reports_an_unreadable_source_as_a_gateway_failure(
    client, db_session, monkeypatch
):
    dead = material(status=ingest.FETCH_FAILED, code="status_not_ok", message="HTTP 500", text="")
    monkeypatch.setattr(snapshots, "ingest_pdf_uri", lambda uri, **kw: replace(dead, uri=uri))

    response = client.post(
        f"{API}/ai/research",
        json={
            "question": QUESTION,
            "sources": [{"kind": "pdf", "uri": "https://example.test/paper.pdf"}],
        },
    )

    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "source_unavailable"
    assert "HTTP 500" in detail["message"]
    assert db_session.scalars(select(AIResearchRun)).one().status == "failed"
    assert db_session.scalars(select(AISourceSnapshot)).one().status == "fetch_failed"


def test_a_fetched_source_reaches_the_model_through_the_endpoint(client, db_session, monkeypatch):
    monkeypatch.setattr(snapshots, "ingest_url", lambda uri, **kw: replace(material(), uri=uri))
    seen: list = []
    patch_pipeline(monkeypatch, [hypothesis_payload("page"), draft_payload("page")], seen=seen)

    response = client.post(
        f"{API}/ai/research",
        json={
            "question": QUESTION,
            "sources": [{"kind": "url", "uri": PAGE, "source_ref": "page"}],
            "model": "test-model",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    summary = payload["sources"][0]
    assert summary["snapshot_id"] == db_session.scalars(select(AISourceSnapshot)).one().id
    assert summary["source_hash"] == hashlib.sha256(b"<html>page</html>").hexdigest()
    assert summary["retention"]["full_text_stored"] is False
    # Third-party material is excerpted by default, so the endpoint reports an
    # excerpt budget smaller than what was read.
    assert summary["retention"]["excerpt_budget"] == service.THIRD_PARTY_EXCERPT_CHARS
    assert [source.text for request in seen for source in request.untrusted_sources] == [
        NOTE,
        NOTE,
    ]

    # The run detail carries the stored snapshot, so one GET answers which
    # material this run was based on (docs/27 §5.2).
    detail = client.get(f"{API}/ai/research/{payload['run_id']}").json()
    snapshot = detail["sources"][0]["snapshot"]
    assert snapshot is not None
    assert snapshot["snapshot_id"] == summary["snapshot_id"]
    assert snapshot["snapshot_status"] == "retained"
    assert snapshot["parse_status"] == "ok"
    assert snapshot["original_uri"] == PAGE
    assert snapshot["text_hash"] == summary["text_hash"]
    assert NOTE[:50] in "".join(snapshot["excerpt"])


def test_only_the_text_given_is_used_when_both_text_and_a_uri_are_present(client, monkeypatch):
    def must_not_fetch(uri, **kwargs):
        raise AssertionError("the platform must not fetch when the caller supplied text")

    monkeypatch.setattr(snapshots, "ingest_url", must_not_fetch)
    patch_pipeline(monkeypatch, [hypothesis_payload("page"), draft_payload("page")])

    response = client.post(
        f"{API}/ai/research",
        json={
            "question": QUESTION,
            "sources": [
                {
                    "kind": "url",
                    "uri": PAGE,
                    "text": NOTE,
                    "source_ref": "page",
                }
            ],
            "model": "test-model",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["sources"][0]["snapshot_id"] is None
    assert [warning["kind"] for warning in payload["warnings"]] == ["text_preferred"]
