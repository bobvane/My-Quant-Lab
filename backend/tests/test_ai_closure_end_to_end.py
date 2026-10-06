"""The v2.4.0 closure, end to end through the HTTP API (Step 1).

Two kinds of proof live here, because they answer different questions.

*The chain* -- provider row -> research run -> draft -> human confirmation ->
compiler -> activation -> the ADR-171 selection the signal scanner uses -- is
driven through the real endpoints, with the model transport stubbed at
``httpx.post``. That keeps the run deterministic while still exercising every
layer between the request and the database.

*The transport* -- that the provider client really opens a socket, sends
``Authorization: Bearer <stored key>`` and reads the usage a server reports --
is checked separately against a throwaway OpenAI-compatible server on
``127.0.0.1``. It is deliberately *not* combined with ``TestClient``: running a
blocking HTTP call to an in-process server from inside the test client's portal
deadlocks (verified with a ``faulthandler`` dump), which is an artefact of the
test harness rather than of the application.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import httpx
import pytest
from research_payloads import MARTIN, NOTE, QUESTION, draft_payload, hypothesis_payload
from sqlalchemy import select

from app.ai.provider import OpenAICompatibleProvider
from app.data.strategy_service import ACTIVATABLE_VALIDATION_STATUSES
from app.domain.models import AITask, AuditLog, StrategyVersion

MODEL = "meta/muse-spark-1.3"
INPUT_PRICE = 0.14
OUTPUT_PRICE = 0.28
PROMPT_TOKENS = 1200
COMPLETION_TOKENS = 300
KEY = "sk-local-stub-key"

#: Answers the stubbed model transport hands out, in call order.
_queued: list[dict[str, Any]] = []
#: Every stubbed transport call, in order.
_seen: list[dict[str, Any]] = []


class _StubResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("stub failure", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


def _answer_post(url: str, *, headers: dict[str, str] | None = None, json: Any = None, **_: Any):
    body = json if isinstance(json, dict) else {}
    _seen.append(
        {
            "method": "POST",
            "url": url,
            "authorization": (headers or {}).get("Authorization"),
            "model": body.get("model"),
        }
    )
    answer = _queued.pop(0)
    return _StubResponse(
        {
            "model": body.get("model") or MODEL,
            "choices": [{"message": {"role": "assistant", "content": json_dumps(answer)}}],
            "usage": {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS},
        }
    )


def _answer_get(url: str, *, headers: dict[str, str] | None = None, **_: Any):
    _seen.append(
        {"method": "GET", "url": url, "authorization": (headers or {}).get("Authorization")}
    )
    return _StubResponse({"data": [{"id": MODEL}]})


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _clean_transport() -> Iterator[None]:
    _queued.clear()
    _seen.clear()
    yield
    _queued.clear()
    _seen.clear()


@pytest.fixture()
def stub_transport(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    monkeypatch.setattr(httpx, "post", _answer_post)
    monkeypatch.setattr(httpx, "get", _answer_get)
    yield _queued


@pytest.fixture()
def local_provider(client, stub_transport) -> dict[str, Any]:
    created = client.post(
        "/api/v1/settings/ai/providers",
        json={
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": KEY,
            "default_model": MODEL,
            "daily_budget_usd": 0.5,
            "models": [
                {
                    "model_name": MODEL,
                    "capability_tier": "high",
                    "input_cost_per_mtok": INPUT_PRICE,
                    "output_cost_per_mtok": OUTPUT_PRICE,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["api_key_set"] is True
    assert KEY not in created.text  # write-only: the key never comes back out
    return body


def _research_body() -> dict[str, Any]:
    """One user-pasted note; the source_ref must match the scripted answers'
    citations, or the run is refused for provenance (ADR-154)."""

    return {
        "question": QUESTION,
        "sources": [
            {
                "kind": "user_input",
                "text": NOTE,
                "source_ref": MARTIN,
                "label": "Martin",
            }
        ],
    }


def _compileable_answers() -> list[dict[str, Any]]:
    """The researcher/architect pair that also survives the frozen compiler.

    ``draft_payload()`` is deliberately vague: it leaves exit, risk, sizing and the
    trading costs unnamed, and the compiler answers ``NEEDS_USER_DECISION`` rather
    than inventing them. This pair keeps the same provenance shape -- same
    hypothesis, same citations, the vague intent still weakened to ``ASSUMED`` --
    but states those decisions in the compiler's canonical form, so one run can be
    walked all the way to a typed ``StrategyVersion``.
    """

    hypothesis = hypothesis_payload()
    draft = draft_payload()
    made_up = "AI 为了能被编译而补上的定义，不是原文写下的规则。"

    draft["strategy_name"] = "BTC oversold rebound (RSI formalization)"
    draft["market"] = {
        "markets": [],
        "asset_classes": ["stock"],
        "timeframes": ["1d"],
        "universe": None,
    }
    draft["rules"] = [
        {
            "id": "d-market",
            "field": "market",
            "statement": "在 BTC 上。",
            "origin": "EXPLICIT",
            "confidence": "high",
            "derived_from": "r-market",
            "evidence": [{"source_ref": MARTIN, "quote": "BTC"}],
        },
        {
            "id": "d-entry",
            "field": "entry",
            "statement": "RSI(14) 跌破 30 时买入。",
            "origin": "ASSUMED",
            "confidence": "low",
            "derived_from": "r-entry",
            "evidence": [{"source_ref": MARTIN, "quote": "超跌之后反弹的时候买入"}],
            "parameters": {
                "left": "RSI",
                "operator": "lt",
                "right": 30,
                "period": 14,
                "side": "long",
            },
        },
        {
            "id": "d-exit",
            "field": "exit",
            "statement": "RSI(14) 涨破 70 时卖出。",
            "origin": "ASSUMED",
            "confidence": "low",
            "parameters": {
                "left": "RSI",
                "operator": "gt",
                "right": 70,
                "period": 14,
                "side": "long",
            },
        },
        {
            "id": "d-risk",
            "field": "risk",
            "statement": "两倍 ATR 止损，最多半仓。",
            "origin": "ASSUMED",
            "confidence": "low",
            "parameters": {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
        },
        {
            "id": "d-sizing",
            "field": "sizing",
            "statement": "每次用三分之一的仓位。",
            "origin": "ASSUMED",
            "confidence": "low",
            "parameters": {"mode": "fixed_fraction", "fraction": 0.3},
        },
        {
            "id": "d-costs",
            "field": "execution",
            "statement": "万分之十的手续费和万分之五的滑点。",
            "origin": "ASSUMED",
            "confidence": "low",
            "parameters": {"fee_bps": 10, "slippage_bps": 5},
        },
    ]
    draft["indicators"] = [
        {
            "name": "RSI",
            "origin": "ASSUMED",
            "parameters": {"period": 14, "input": "close"},
            "note": made_up,
        }
    ]
    draft["unknowns"] = [
        {
            "field": "timeframe",
            "rule_id": "r-timeframe",
            "why": "材料没有说明周期；草案替它选了一个，所以编译时不再算未决。",
            "needed_to_formalize": False,
        }
    ]
    draft["parameters"] = {}
    draft["assumptions"] = [
        {
            "statement": made_up,
            "applies_to": [
                "entry",
                "exit",
                "risk",
                "sizing",
                "execution",
                "timeframe",
                "indicator",
            ],
            "reason": "材料只说了买入的时机，其余都要由 AI 补上才能被编译。",
        }
    ]
    return [hypothesis, draft]


def test_the_whole_closure_runs_through_the_api(client, db_session, local_provider):
    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is True
    assert status["provider_name"] == "openrouter"
    assert status["spent_today_usd"] == 0.0

    _queued.extend(_compileable_answers())

    run = client.post("/api/v1/ai/research", json=_research_body())
    assert run.status_code == 200, run.text
    payload = run.json()
    assert payload["status"] == "completed"
    assert payload["draft"] is not None
    assert payload["draft"]["confirmation"] is None

    run_id = payload["run_id"]
    draft_id = payload["draft"]["draft_id"]

    # 1) The model calls went out as real HTTP requests with the stored key.
    assert [call["url"] for call in _seen] == [
        "https://openrouter.ai/api/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
    ]
    assert {call["authorization"] for call in _seen} == {f"Bearer {KEY}"}
    assert {call["model"] for call in _seen} == {MODEL}

    # 2) The usage the provider reported became real, non-fake cost.
    tasks = list(db_session.scalars(select(AITask).order_by(AITask.id)))
    assert len(tasks) == 2
    for task in tasks:
        assert task.status == "completed"
        usage = task.token_usage_json or {}
        assert usage["input_tokens"] == PROMPT_TOKENS
        assert usage["output_tokens"] == COMPLETION_TOKENS
        assert usage["reported_by_provider"] is True
        assert usage["estimated"] is False
        assert task.cost_usd == Decimal("0.000252")  # 1200/1e6*0.14 + 300/1e6*0.28

    # 3) A draft is stored, and nothing can execute it yet.
    detail = client.get(f"/api/v1/ai/research/{run_id}").json()
    assert detail["draft"]["draft_id"] == draft_id
    assert detail["draft"]["executable"] is False
    assert detail["draft"]["compiled_strategy_version_id"] is None
    assert db_session.scalars(select(StrategyVersion)).all() == []

    # 4) The human gate records a decision and creates no version.
    confirmed = client.post(
        f"/api/v1/ai/strategy/drafts/{draft_id}/confirmations",
        json={"decision": "confirmed", "note": "walked through the closure by hand"},
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["strategy_version_created"] is False
    assert db_session.scalars(select(StrategyVersion)).all() == []

    # 5) The compiler turns the confirmed draft into a version that is valid...
    strategy_id = client.post("/api/v1/strategies", json={"name": "Closure target"}).json()["id"]
    compiled = client.post(
        f"/api/v1/ai/strategy/drafts/{draft_id}/compile", json={"strategy_id": strategy_id}
    )
    assert compiled.status_code == 201, compiled.text
    version_id = compiled.json()["strategy_version_id"]
    version = db_session.get(StrategyVersion, version_id)
    assert version.validation_status == "valid"
    # ...and deliberately still not live: no AI endpoint activates anything.
    assert version.is_current is False
    assert (
        db_session.scalars(
            select(StrategyVersion).where(StrategyVersion.is_current.is_(True))
        ).all()
        == []
    )

    # 6) Only the explicit human activation puts it on the ADR-171 signal path.
    activated = client.put(f"/api/v1/strategy-versions/{version_id}/activate")
    assert activated.status_code == 200, activated.text
    db_session.expire_all()
    assert db_session.get(StrategyVersion, version_id).is_current is True
    scannable = db_session.scalars(
        select(StrategyVersion).where(
            StrategyVersion.is_current.is_(True),
            StrategyVersion.validation_status.in_(ACTIVATABLE_VALIDATION_STATUSES),
        )
    ).all()
    assert [row.id for row in scannable] == [version_id]

    # 7) Every step left an audit trail.
    events = [
        row.event_type
        for row in db_session.scalars(select(AuditLog).order_by(AuditLog.id))
        if row.event_type.startswith(("strategy_draft_", "strategy_version_"))
    ]
    assert events == [
        "strategy_draft_confirmed",
        "strategy_version_created",
        "strategy_version_activated",
    ]


def test_without_a_live_provider_the_research_endpoint_says_so(client, db_session, local_provider):
    deleted = client.delete(f"/api/v1/settings/ai/providers/{local_provider['id']}")
    assert deleted.status_code in (200, 204)
    _queued.extend([hypothesis_payload(), draft_payload()])

    response = client.post("/api/v1/ai/research", json=_research_body())

    assert response.status_code == 503
    assert (
        response.json()["detail"]
        == "AI provider not configured; configure one under Settings to enable explanations"
    )
    assert _seen == []


def test_the_connection_test_talks_to_the_provider(client, local_provider):
    response = client.post(f"/api/v1/settings/ai/providers/{local_provider['id']}/test")

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert _seen == [
        {
            "method": "GET",
            "url": "https://openrouter.ai/api/v1/models",
            "authorization": f"Bearer {KEY}",
        }
    ]


def test_the_reported_usage_shows_up_in_the_spent_figure(client, local_provider):
    """The daily budget is no longer decorative: a real call moves the number."""

    _queued.extend([hypothesis_payload(), draft_payload()])
    assert client.post("/api/v1/ai/research", json=_research_body()).status_code == 200

    status = client.get("/api/v1/ai/status").json()

    assert status["tasks_today"] == 2
    assert status["spent_today_usd"] == pytest.approx(2 * 0.000252)


class _StubServer(BaseHTTPRequestHandler):
    """A real socket endpoint, used only by the transport test below."""

    protocol_version = "HTTP/1.1"

    answers: ClassVar[list[dict[str, Any]]] = []
    calls: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server's naming
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        _StubServer.calls.append(
            {"path": self.path, "authorization": self.headers.get("Authorization")}
        )
        answer = _StubServer.answers.pop(0)
        raw = json_dumps(
            {
                "model": body.get("model") or MODEL,
                "choices": [{"message": {"role": "assistant", "content": json_dumps(answer)}}],
                "usage": {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        return


def test_the_real_client_speaks_http_to_a_real_socket():
    _StubServer.answers = [{"ok": True}]
    _StubServer.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAICompatibleProvider(
            f"http://127.0.0.1:{server.server_port}/v1", KEY, timeout=5.0
        )
        reply = client.reply([{"role": "user", "content": "hello"}], model=MODEL)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert json.loads(reply.text) == {"ok": True}
    assert reply.model == MODEL
    assert reply.usage == {"input_tokens": PROMPT_TOKENS, "output_tokens": COMPLETION_TOKENS}
    assert reply.reported_usage is True
    assert client.last_reply is reply
    assert _StubServer.calls == [{"path": "/v1/chat/completions", "authorization": f"Bearer {KEY}"}]
