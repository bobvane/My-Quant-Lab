"""The compile endpoint: a stored ``StrategyDraft`` in, one version or a refusal out.

Step 2C is integration only. ``app/compiler`` decides (``docs/29`` §16.7) and this
module pins the part the endpoint owns, which is the database: ``COMPILED`` creates
exactly one ``StrategyVersion`` whose ``evidence_json.compile_report`` is the report the
compiler returned and points the draft at it, while ``NEEDS_USER_DECISION`` and
``REJECTED`` create nothing at all. The body may name a target and nothing else -- no
spec, no hash, no compiler version -- and the version itself comes from the project's
own ``next_version`` rule (ADR-061) rather than one invented in the router.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from research_payloads import draft_payload
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.ai import confirmation as confirmation_service
from app.ai.research_schemas import CapabilityDecision
from app.data import strategy_service
from app.domain.models import (
    AIResearchRun,
    Strategy,
    StrategyDraft,
    StrategyHypothesis,
    StrategyVersion,
)

COMPILE_URL = "/api/v1/ai/strategy/drafts/{draft_id}/compile"

# The five codes ``docs/29`` §17.2 fixes for Martin's material, and its pinned draft hash.
MARTIN_CODES = frozenset(
    {
        "rule_unmapped",
        "parameter_invalid",
        "not_expressible",
        "missing_required_slot",
        "unknown_blocks_slot",
    }
)
MARTIN_DRAFT_HASH = "b736374722d905936044135d10fa1039d87fb80b067ba932557dd65d414121f3"

# A spec that parses, for the version history a compile has to continue (or refuse).
LEGACY_DSL: dict[str, Any] = {
    "schema_version": "1.0",
    "strategy": {"id": "legacy", "name": "Legacy", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _payload() -> dict[str, Any]:
    """A draft that carries every decision the compiler refuses to assume."""

    return {
        "strategy_name": "RSI mean reversion",
        "status": "SUPPORTED",
        "market": {
            "markets": [],
            "asset_classes": ["stock"],
            "timeframes": ["1d"],
            "universe": None,
        },
        "rules": [
            {
                "id": "e-oversold",
                "field": "entry",
                "statement": "buy when RSI(14) drops below 30",
                "origin": "EXPLICIT",
                "parameters": {
                    "left": "RSI",
                    "operator": "lt",
                    "right": 30,
                    "period": 14,
                    "side": "long",
                },
            },
            {
                "id": "x-overbought",
                "field": "exit",
                "statement": "sell when RSI(14) rises above 70",
                "origin": "EXPLICIT",
                "parameters": {
                    "left": "RSI",
                    "operator": "gt",
                    "right": 70,
                    "period": 14,
                    "side": "long",
                },
            },
            {
                "id": "k-risk",
                "field": "risk",
                "statement": "two ATR of stop, half of the account",
                "origin": "EXPLICIT",
                "parameters": {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
            },
            {
                "id": "s-sizing",
                "field": "sizing",
                "statement": "a third of the account at a time",
                "origin": "EXPLICIT",
                "parameters": {"mode": "fixed_fraction", "fraction": 0.3},
            },
            {
                "id": "c-costs",
                "field": "execution",
                "statement": "ten basis points of fee and five of slippage",
                "origin": "EXPLICIT",
                "parameters": {"fee_bps": 10, "slippage_bps": 5},
            },
        ],
        "indicators": [
            {"name": "RSI", "origin": "EXPLICIT", "parameters": {"period": 14, "input": "close"}}
        ],
        "unknowns": [],
        "required_capabilities": [],
        "experimental_alternatives": [],
        "assumptions": [],
        "parameters": {},
        "notes": [],
        "understanding_of_original": "An RSI mean reversion strategy.",
    }


def _drifted_payload() -> dict[str, Any]:
    """The same draft plus one condition on a column the validator knows and the engine
    never materialises, so the compiler's own guard answers ``REJECTED``."""

    payload = _payload()
    payload["rules"][0]["parameters"]["combine"] = "all"
    payload["rules"].append(
        {
            "id": "e-drifted",
            "field": "entry",
            "statement": "also buy above the previous high",
            "origin": "EXPLICIT",
            "parameters": {
                "left": "close",
                "operator": "gt",
                "right": "previous_high",
                "side": "long",
                "combine": "all",
            },
        }
    )
    return payload


def _url(draft_id: int) -> str:
    return COMPILE_URL.format(draft_id=draft_id)


def _store_strategy(db_session, name: str = "Compiled target") -> Strategy:
    strategy = Strategy(name=name, slug=name.lower().replace(" ", "-"))
    db_session.add(strategy)
    db_session.flush()
    db_session.commit()
    return strategy


def _store_draft(
    db_session,
    payload: dict[str, Any],
    *,
    capability: dict[str, Any] | None = None,
    confirmed: bool = True,
) -> StrategyDraft:
    """A stored draft, built the way the research layer builds one (`ai/research.py`).

    ``confirmed`` records the human decision the compile endpoint requires before it
    will call the compiler (v2.5.0 Step 2). These tests are about the compiler, so the
    fixture answers for the human by default; the gate's own edges are tested in
    `test_draft_confirmation.py`.
    """

    run = AIResearchRun(question="why does this strategy work?", status="drafted")
    db_session.add(run)
    db_session.flush()
    hypothesis = StrategyHypothesis(run_id=run.id, strategy_name="RSI mean reversion")
    db_session.add(hypothesis)
    db_session.flush()
    draft = StrategyDraft(
        run_id=run.id,
        hypothesis_id=hypothesis.id,
        status="SUPPORTED",
        capability_status="SUPPORTED",
        draft_json=payload,
        capability_report_json=capability,
    )
    db_session.add(draft)
    db_session.flush()
    if confirmed:
        confirmation_service.record_confirmation(db_session, draft=draft, decision="confirmed")
    db_session.commit()
    return draft


def _version_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(StrategyVersion)) or 0


def test_the_draft_fixture_is_a_schema_valid_draft() -> None:
    """The fixture must be a draft the research layer could have stored, not a shortcut."""

    from app.ai.research_schemas import parse_draft

    parsed = parse_draft(_payload())

    assert parsed.strategy_name == "RSI mean reversion"


# --- A. COMPILED -------------------------------------------------------------------


def test_compiled_creates_one_strategy_version(client, db_session) -> None:
    draft = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 201
    body = response.json()
    assert body["result"] == "COMPILED"
    assert body["strategy_id"] == strategy.id
    assert body["version"] == "1.0.0"
    assert body["report"]["target"] == {"strategy_id": strategy.id, "version": "1.0.0"}
    assert body["compile_hash"] == body["report"]["draft"]["compile_hash"]

    versions = db_session.scalars(select(StrategyVersion)).all()
    assert len(versions) == 1
    version = versions[0]
    assert body["strategy_version_id"] == version.id
    assert version.strategy_id == strategy.id
    assert version.version == "1.0.0"
    assert version.validation_status == "valid"
    # The compiler's report is stored, not a second one written by the API layer.
    assert version.evidence_json == {"compile_report": body["report"]}
    # The spec is the compiler's, and the sealed hash is the project's own function.
    assert version.dsl_json["schema_version"] == "1.0"
    assert version.dsl_json["strategy"]["source"]["type"] == "compiler"
    assert version.immutable_hash == strategy_service.immutable_hash(
        version.dsl_json, version.version
    )
    # The back-reference is written only once the version exists.
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id == version.id
    assert db_session.get(Strategy, strategy.id).lifecycle == "normalized"


# --- B. NEEDS_USER_DECISION --------------------------------------------------------


def test_needs_user_decision_writes_nothing(client, db_session) -> None:
    draft = _store_draft(db_session, draft_payload())
    strategy = _store_strategy(db_session)

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 422
    body = response.json()
    assert body["result"] == "NEEDS_USER_DECISION"
    assert {row["code"] for row in body["report"]["rejections"]} == MARTIN_CODES
    assert body["report"]["draft"]["draft_hash"] == MARTIN_DRAFT_HASH
    assert _version_count(db_session) == 0
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None
    assert db_session.get(Strategy, strategy.id).lifecycle == "imported"


# --- C. REJECTED -------------------------------------------------------------------


def test_rejected_writes_nothing(client, db_session) -> None:
    draft = _store_draft(db_session, _drifted_payload())
    strategy = _store_strategy(db_session)

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 422
    body = response.json()
    assert body["result"] == "REJECTED"
    assert {row["code"] for row in body["report"]["rejections"]} == {"engine_incompatible"}
    assert "previous_high" in json.dumps(body["report"])
    assert _version_count(db_session) == 0
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None


# --- D. 404 ------------------------------------------------------------------------


def test_an_unknown_draft_is_a_404(client, db_session) -> None:
    strategy = _store_strategy(db_session)

    response = client.post(_url(999_999), json={"strategy_id": strategy.id})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "draft_not_found"
    assert _version_count(db_session) == 0


def test_an_unknown_strategy_is_a_404(client, db_session) -> None:
    draft = _store_draft(db_session, _payload())

    response = client.post(_url(draft.id), json={"strategy_id": 999_999})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "strategy_not_found"
    assert _version_count(db_session) == 0
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None


# --- E. 409 / 422, in the project's own error shapes --------------------------------


def test_compiling_a_bound_draft_again_is_a_409(client, db_session) -> None:
    draft = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)
    first = client.post(_url(draft.id), json={"strategy_id": strategy.id})
    assert first.status_code == 201

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "draft_already_compiled"
    assert body["error"]["details"] == {
        "strategy_version_id": first.json()["strategy_version_id"],
        "version": "1.0.0",
    }
    # ADR-169: a 409 is an input-boundary error -- there is no compile report to carry.
    assert "result" not in body
    assert "report" not in body
    assert _version_count(db_session) == 1


def test_a_history_that_cannot_be_incremented_is_a_409(client, db_session) -> None:
    strategy = _store_strategy(db_session)
    strategy_service.create_strategy_version(db_session, strategy, version="v2", dsl=LEGACY_DSL)
    draft = _store_draft(db_session, _payload())

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "version_unassignable"
    assert body["error"]["details"] == {"strategy_id": strategy.id, "versions": ["v2"]}
    assert "result" not in body
    assert "report" not in body
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None


def test_the_body_may_not_carry_a_spec(client, db_session) -> None:
    draft = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)

    smuggled = client.post(
        _url(draft.id),
        json={"strategy_id": strategy.id, "dsl": LEGACY_DSL, "compile_hash": "0" * 64},
    )
    zero = client.post(_url(draft.id), json={"strategy_id": 0})

    assert smuggled.status_code == 422
    assert zero.status_code == 422
    assert _version_count(db_session) == 0
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None


# --- F. transaction boundary -------------------------------------------------------


def test_a_failure_after_the_version_row_leaves_no_half_state(
    client, db_session, monkeypatch
) -> None:
    """The version row is flushed before the audit event, so the rollback has real work
    to do: a session that failed here must not leave a version, or a bound draft."""

    draft = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)

    def _rejected(db, **kwargs):  # noqa: ANN001, ANN003 - a stand-in for the real writer
        raise IntegrityError("INSERT INTO audit_log", {}, Exception("audit log refused"))

    monkeypatch.setattr(strategy_service, "record_audit", _rejected)

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "version_conflict"
    assert "result" not in body
    assert "report" not in body
    assert _version_count(db_session) == 0
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None


def test_commit_false_hands_the_transaction_to_the_caller(db_session) -> None:
    strategy = _store_strategy(db_session)

    version = strategy_service.create_strategy_version(
        db_session, strategy, version="1.0.0", dsl=LEGACY_DSL, commit=False
    )

    assert version.id is not None
    assert _version_count(db_session) == 1
    db_session.rollback()
    assert _version_count(db_session) == 0


# --- G. a second draft for the same strategy ---------------------------------------


def test_a_second_draft_for_the_same_strategy_takes_the_next_version(client, db_session) -> None:
    first = _store_draft(db_session, _payload())
    second = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)

    first_response = client.post(_url(first.id), json={"strategy_id": strategy.id})
    # One session serves both requests here, while the application hands each request its
    # own; expiring reloads the history the way a fresh session would.
    db_session.expire_all()
    second_response = client.post(_url(second.id), json={"strategy_id": strategy.id})

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["version"] == "1.0.0"
    assert second_response.json()["version"] == "1.0.1"

    versions = db_session.scalars(select(StrategyVersion).order_by(StrategyVersion.id)).all()
    assert [row.version for row in versions] == ["1.0.0", "1.0.1"]
    # ADR-171 changed this line on purpose: compiling no longer activates, so both
    # versions stay inactive until a human activates one. It used to read
    # `[False, True]` -- the compiler inherited `make_current=True` from the service.
    assert [row.is_current for row in versions] == [False, False]
    assert db_session.get(StrategyDraft, first.id).compiled_strategy_version_id == versions[0].id
    assert db_session.get(StrategyDraft, second.id).compiled_strategy_version_id == versions[1].id


# --- H. the sealed version, and the capability report as input only ------------------


def test_the_compiled_version_is_still_sealed(client, db_session) -> None:
    draft = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)
    body = client.post(_url(draft.id), json={"strategy_id": strategy.id}).json()
    version = db_session.get(StrategyVersion, body["strategy_version_id"])

    with pytest.raises(IntegrityError, match="immutable"):
        db_session.execute(
            text("UPDATE strategy_versions SET dsl_json = :dsl WHERE id = :id"),
            {"dsl": '{"tampered": true}', "id": version.id},
        )
    db_session.rollback()

    assert db_session.get(StrategyVersion, version.id).immutable_hash == version.immutable_hash


def test_capability_diagnostics_do_not_replace_the_compiler(client, db_session) -> None:
    """The registry is an input the compiler reads; it is never an early exit here.

    The report is stored the way the research layer stores it (ADR-170):
    ``CapabilityDecision.as_dict()``, whose key is ``verdict`` rather than the registry's
    own ``status``.
    """

    capability = CapabilityDecision(
        verdict="NEEDS_CAPABILITY",
        requested=("market.universe",),
        missing=("market.universe",),
        reasons={"market.universe": "no cross-sectional universe in DSL 1.0"},
    ).as_dict()
    assert "status" not in capability
    draft = _store_draft(db_session, _payload(), capability=capability)
    strategy = _store_strategy(db_session)

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 422
    body = response.json()
    assert body["result"] == "REJECTED"
    assert {row["code"] for row in body["report"]["rejections"]} == {"capability_missing"}
    assert body["report"]["capability"] == capability
    assert _version_count(db_session) == 0
    assert db_session.get(StrategyDraft, draft.id).compiled_strategy_version_id is None


def test_a_supported_capability_report_compiles(client, db_session) -> None:
    """A real ``SUPPORTED`` report with empty lists is not a rejection (ADR-170)."""

    capability = CapabilityDecision(verdict="SUPPORTED", requested=("indicators",)).as_dict()
    draft = _store_draft(db_session, _payload(), capability=capability)
    strategy = _store_strategy(db_session)

    response = client.post(_url(draft.id), json={"strategy_id": strategy.id})

    assert response.status_code == 201, response.json()
    assert response.json()["result"] == "COMPILED"
    assert _version_count(db_session) == 1


# --- I. the document a generated client reads ---------------------------------------


def test_the_openapi_document_declares_the_refusals(client) -> None:
    """A refusal the document does not describe is one a client cannot handle.

    ``_compile_error`` answers a 404/409 itself, so FastAPI cannot infer either shape
    from a model: without the declaration the document offered only 201 and 422, and
    the five codes docs/29 §16.7 freezes lived nowhere but in the contract and in this
    module's own tests -- which a generated client never reads.
    """

    responses = client.get("/openapi.json").json()["paths"][COMPILE_URL]["post"]["responses"]

    assert {"201", "404", "409", "422"} <= set(responses)
    assert "draft_not_found" in responses["404"]["description"]
    assert "strategy_not_found" in responses["404"]["description"]
    for code in (
        "draft_already_compiled",
        "draft_not_confirmed",
        "version_unassignable",
        "version_conflict",
    ):
        assert code in responses["409"]["description"], code
    envelope = responses["409"]["content"]["application/json"]["schema"]
    assert envelope["properties"]["error"]["required"] == ["code", "message", "details"]
    assert set(envelope["properties"]["error"]["properties"]) == {"code", "message", "details"}


# --- J. the compiled version is not the active one (ADR-171) ------------------------


def test_a_compiled_version_does_not_become_current_by_itself(client, db_session) -> None:
    """Compiling produces a version; *activating* it is a separate, explicit act.

    ADR-171: the compiled version is created with ``make_current=False``. Until it was,
    an AI-originated draft reached the signal path without anyone deciding to promote it
    (the endpoint simply inherited the service's ``make_current=True`` default), which is
    the one path ``docs/15``'s Phase 8 acceptance says must not exist. Activation now
    requires ``validation_status == "valid"``; this guard pins the other half -- that
    compiling never activates.
    """

    draft = _store_draft(db_session, _payload())
    strategy = _store_strategy(db_session)

    body = client.post(_url(draft.id), json={"strategy_id": strategy.id}).json()
    version = db_session.get(StrategyVersion, body["strategy_version_id"])

    assert version.validation_status == "valid"
    assert version.is_current is False

    activated = client.put(f"/api/v1/strategy-versions/{version.id}/activate")

    assert activated.status_code == 200
    assert activated.json()["is_current"] is True
