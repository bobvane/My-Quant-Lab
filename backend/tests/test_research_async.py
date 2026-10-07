"""Research runs that finish off the request path (docs/26 C10).

A research run makes two model calls, which does not fit inside a proxy's
patience. The request therefore reads and stores the material, hands the run to
a worker, and answers 202; ``GET /ai/research/{run_id}`` reports the rest. That
split only works if three things stay true, and both are tested here from the
outside:

* the run row is the whole state that crosses the process boundary -- what is
  queued is an id, not a closure over a live session;
* a request that cannot be served is never queued (no provider, a refused
  source, an unreadable source): a worker would only rediscover the same answer
  and bill nobody for it;
* a worker crash is recorded on the run and committed *before* the exception is
  re-raised, so a run cannot stay ``running`` for ever after a redis delivery.

The worker task is called as a plain function with ``session_scope`` replaced by
the test session -- the same convention ``test_github_watch.py`` uses. Nothing
here touches the network: the provider below the real router is a script.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from research_payloads import (
    MARTIN,
    NOTE,
    QUESTION,
    RecordingProvider,
    draft_payload,
    hypothesis_payload,
    make_provider,
    variant,
)
from sqlalchemy import select

from app.ai import research as research_service
from app.api.routers import ai as ai_router
from app.core.config import settings
from app.domain.models import AIProvider, AIResearchRun
from app.workers import tasks as worker_tasks

API = "/api/v1"
RESEARCH = f"{API}/ai/research"


@pytest.fixture
def provider(db_session) -> AIProvider:
    return make_provider(db_session)


@pytest.fixture
def handed_over(monkeypatch) -> list[tuple[int, str | None]]:
    """Keep what the request handed to the worker instead of talking to a broker."""

    handed: list[tuple[int, str | None]] = []
    monkeypatch.setattr(settings, "ai_research_async", True)
    monkeypatch.setattr(
        ai_router,
        "_enqueue_research",
        lambda run_id, model=None: handed.append((run_id, model)),
    )
    return handed


@contextmanager
def worker_scope(db_session):
    """``session_scope`` as the worker sees it: the test's own session."""

    yield db_session


@contextmanager
def scripted_provider(monkeypatch, outputs: list[dict]):
    """Answer the model calls from a script, below the real router."""

    from app.ai import runtime as runtime_module

    RecordingProvider.queue = list(outputs)
    RecordingProvider.instances = []
    monkeypatch.setattr(runtime_module, "OpenAICompatibleProvider", RecordingProvider)
    try:
        yield
    finally:
        RecordingProvider.queue = []


def queued_run(db_session, *, question: str = QUESTION) -> AIResearchRun:
    """A run waiting for its worker, as ``prepare_research(queued=True)`` leaves it."""

    run = AIResearchRun(question=question, status="queued", current_step="queued")
    db_session.add(run)
    db_session.commit()
    return run


def material_for_test(**overrides):
    """One observation, as the ingester returns it (see ``test_source_research.py``)."""

    from app.sources.ingest import IngestedMaterial

    fields: dict = {"kind": "url", "status": "blocked", "parse_status": "not_parsed"}
    fields.update(overrides)
    return IngestedMaterial(**fields)


def test_a_request_answers_202_and_hands_the_run_to_the_worker(
    client, db_session, provider, handed_over
) -> None:
    response = client.post(
        RESEARCH,
        json={
            "question": variant(QUESTION, "queued"),
            "sources": [{"text": NOTE, "source_ref": MARTIN}],
            "model": "test-model",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["current_step"] == "queued"
    # The material was already read and stored, so the worker is handed an id.
    assert handed_over == [(body["run_id"], "test-model")]
    run = db_session.get(AIResearchRun, body["run_id"])
    assert run is not None
    assert run.sources_json
    # No model was asked anything on the request path.
    assert run.researcher_task_id is None
    assert run.architect_task_id is None


def test_the_queued_run_carries_its_status_until_the_worker_is_done(
    client, db_session, provider, handed_over
) -> None:
    response = client.post(
        RESEARCH,
        json={
            "question": variant(QUESTION, "follow"),
            "sources": [{"text": NOTE, "source_ref": MARTIN}],
        },
    )
    run_id = response.json()["run_id"]

    waiting = client.get(f"{RESEARCH}/{run_id}")
    assert waiting.status_code == 200
    assert waiting.json()["status"] == "queued"
    assert waiting.json()["completed_at"] is None


def test_a_deployment_with_no_provider_never_queues_anything(
    client, db_session, handed_over
) -> None:
    response = client.post(
        RESEARCH,
        json={
            "question": variant(QUESTION, "unconfigured"),
            "sources": [{"text": NOTE, "source_ref": MARTIN}],
        },
    )

    assert response.status_code == 503
    assert handed_over == []
    # The refusal is still on the record: the request happened, and why it could
    # not be answered is part of the answer.
    runs = db_session.scalars(select(AIResearchRun)).all()
    assert [run.status for run in runs] == ["failed"]
    assert runs[0].error_message


def test_a_source_that_was_refused_is_never_queued(
    client, db_session, provider, handed_over, monkeypatch
) -> None:
    """A policy refusal is decided on the request path, not by a worker."""

    from dataclasses import replace

    from app.data import source_snapshot_service as snapshots
    from app.sources import ingest

    blocked = material_for_test(
        status=ingest.BLOCKED, code="address_not_allowed", text="", parse_status="not_parsed"
    )
    monkeypatch.setattr(snapshots, "ingest_url", lambda uri, **kw: replace(blocked, uri=uri))

    response = client.post(
        RESEARCH,
        json={
            "question": variant(QUESTION, "refused"),
            "sources": [{"kind": "url", "uri": "https://example.test/paper", "source_ref": "page"}],
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "source_blocked"
    assert handed_over == []
    runs = db_session.scalars(select(AIResearchRun)).all()
    assert [run.status for run in runs] == ["rejected"]


def test_the_worker_turns_a_queued_run_into_a_finished_one(
    client, db_session, provider, handed_over, monkeypatch
) -> None:
    response = client.post(
        RESEARCH,
        json={
            "question": variant(QUESTION, "finished"),
            "sources": [{"text": NOTE, "source_ref": MARTIN}],
        },
    )
    run_id = response.json()["run_id"]
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: worker_scope(db_session))

    with scripted_provider(monkeypatch, [hypothesis_payload(), draft_payload()]):
        summary = worker_tasks.run_research(run_id)

    assert summary["status"] == "completed"
    run = db_session.get(AIResearchRun, run_id)
    assert run.status == "completed"
    assert run.draft_id is not None
    assert run.completed_at is not None

    finished = client.get(f"{RESEARCH}/{run_id}")
    assert finished.status_code == 200
    assert finished.json()["status"] == "completed"
    assert finished.json()["draft"] is not None


def test_the_worker_leaves_a_run_that_already_decided_alone(db_session, monkeypatch) -> None:
    """A duplicate delivery must be free, not a second bill."""

    run = AIResearchRun(question=QUESTION, status="completed", current_step="completed")
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: worker_scope(db_session))

    def explode(*args, **kwargs):  # pragma: no cover - the failure is the point
        raise AssertionError("a run that already has a verdict must not be re-run")

    monkeypatch.setattr(research_service, "execute_research", explode)

    summary = worker_tasks.run_research(run.id)

    assert summary["skipped"] == "already decided"
    assert summary["status"] == "completed"


def test_the_worker_reports_a_run_that_is_not_there(db_session, monkeypatch) -> None:
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: worker_scope(db_session))

    assert worker_tasks.run_research(10_000_000)["status"] == "missing"


def test_a_crash_inside_the_worker_is_left_on_the_run(db_session, monkeypatch) -> None:
    run = queued_run(db_session, question=variant(QUESTION, "crash"))
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: worker_scope(db_session))

    def boom(db, run, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(research_service, "execute_research", boom)

    with pytest.raises(RuntimeError, match="provider exploded"):
        worker_tasks.run_research(run.id)

    stored = db_session.get(AIResearchRun, run.id)
    assert stored.status == "failed"
    assert stored.current_step == "queued"
    assert "RuntimeError: provider exploded" in stored.error_message
    assert stored.completed_at is not None


def test_a_failure_never_overwrites_a_run_that_already_decided(db_session) -> None:
    run = AIResearchRun(question=QUESTION, status="rejected", current_step="ingest")
    db_session.add(run)
    db_session.commit()

    research_service.mark_research_failed(db_session, run, RuntimeError("too late"))

    assert run.status == "rejected"
    assert run.current_step == "ingest"
    assert run.error_message is None


def test_the_inline_path_still_answers_with_the_finished_run(
    client, db_session, provider, monkeypatch
) -> None:
    """``AI_RESEARCH_ASYNC=false`` is the deployment with no worker (and every test)."""

    monkeypatch.setattr(settings, "ai_research_async", False)

    with scripted_provider(monkeypatch, [hypothesis_payload(), draft_payload()]):
        response = client.post(
            RESEARCH,
            json={
                "question": variant(QUESTION, "inline"),
                "sources": [{"text": NOTE, "source_ref": MARTIN}],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["draft"] is not None
    assert db_session.get(AIResearchRun, body["run_id"]).status == "completed"
