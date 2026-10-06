"""The activation gate: only a ``valid`` version may drive anything (ADR-171).

``validation_status`` was written by the service and then never enforced: activation
flipped ``is_current`` without looking at it, ``make_current`` defaulted to true, the
compile endpoint inherited that default, and both scanner paths selected versions by
``is_current`` alone. These tests pin the frozen contract of the gate. They are **red
until Step 1 implements it** -- that is the point of freezing the contract first.

The contract (ADR-171, ``docs/19`` §5.5):

1. ``ACTIVATABLE_VALIDATION_STATUSES`` in ``app.data.strategy_service`` is the single
   predicate every write path consults, and it is exactly ``("valid",)``.
2. ``PUT /strategy-versions/{id}/activate`` refuses a non-valid version with 422 and the
   sentence the existing backtest gate already uses -- ``strategy version is '<status>',
   not 'valid'`` -- and audits the refusal as ``strategy_version_activation_rejected``.
3. ``POST /strategies/{id}/versions`` honours ``make_current`` only for a valid version:
   an invalid DSL with ``make_current`` true is refused and writes **nothing**. Recording
   an invalid version for the ledger stays possible with ``make_current=false``.
4. The scanner selects ``is_current AND validation_status == 'valid'``.
5. A compiled version never becomes current by itself (pinned in
   ``backend/tests/test_compiler_api.py``); activation is a separate, explicit act.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import (
    Asset,
    MarketDataSeries,
    MarketDataSource,
    Strategy,
    StrategyVersion,
)
from app.simulation.signal_engine import scan_all

VALID_DSL: dict[str, Any] = {
    "schema_version": "1.0",
    "strategy": {"id": "gate", "name": "Gate", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

# Parses fine, then fails static validation on exactly one error: `unknown_column`
# for `nope` (validator.py:235-237). The distinction matters -- the gate is about
# ``validation_status``, not about being unparseable.
INVALID_DSL: dict[str, Any] = {
    **VALID_DSL,
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "nope"}]}},
}

# The sentence ADR-171 freezes. It is the wording `backtests.py:56-60` already uses,
# so one phrase identifies "this version may not drive anything" everywhere.
NOT_VALID = "strategy version is '{status}', not 'valid'"


def _strategy(client, name: str) -> dict[str, Any]:
    response = client.post("/api/v1/strategies", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _create_version(client, strategy_id: int, version: str, dsl: dict, **extra: Any):
    return client.post(
        f"/api/v1/strategies/{strategy_id}/versions",
        json={"version": version, "dsl": dsl, **extra},
    )


def _versions(client, strategy_id: int) -> list[dict[str, Any]]:
    return client.get(f"/api/v1/strategy-versions?strategy_id={strategy_id}").json()


def _stored_version(
    db, strategy_id: int, *, version: str, status: str, is_current: bool = True
) -> StrategyVersion:
    """A row with a status the service itself can no longer produce.

    ``pending`` is the column default (``domain/models.py:232``); only the service
    writes versions in production, and it writes ``valid``/``invalid``. A guard has
    to be able to see the legacy row too, so it is inserted directly.
    """

    row = StrategyVersion(
        strategy_id=strategy_id,
        version=version,
        dsl_json=VALID_DSL,
        immutable_hash="g" * 64,
        validation_status=status,
        is_current=is_current,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- 1. one predicate for all three write paths -------------------------------------


def test_the_only_activatable_status_is_valid() -> None:
    from app.data import strategy_service

    assert getattr(strategy_service, "ACTIVATABLE_VALIDATION_STATUSES", None) == ("valid",)


# --- 2. the activate endpoint -------------------------------------------------------


def test_activating_an_invalid_version_is_refused(client) -> None:
    strategy = _strategy(client, "Gate invalid activate")
    current = _create_version(client, strategy["id"], "1.0.0", VALID_DSL).json()
    invalid = _create_version(
        client, strategy["id"], "1.1.0", INVALID_DSL, make_current=False
    ).json()
    assert invalid["validation_status"] == "invalid"
    assert invalid["is_current"] is False

    refused = client.put(f"/api/v1/strategy-versions/{invalid['id']}/activate")

    assert refused.status_code == 422
    assert refused.json()["detail"] == NOT_VALID.format(status="invalid")
    listed = {row["id"]: row for row in _versions(client, strategy["id"])}
    assert listed[invalid["id"]]["is_current"] is False
    assert listed[current["id"]]["is_current"] is True


def test_activating_a_pending_version_is_refused(client, db_session) -> None:
    """``pending`` is the column default, so a legacy row can exist in that state.

    The shape matters: that row is *not* current. Only the service writes versions in
    production and it writes ``valid``/``invalid``, so no code path ever made a
    ``pending`` version current -- asserting otherwise would demand that a refusal
    retroactively un-set a flag this test had set itself. What the guard must show is
    that the refused activation grants nothing and disturbs nothing: the legacy row
    stays inactive and the existing current version keeps the role.
    """

    strategy = _strategy(client, "Gate pending activate")
    current = _create_version(client, strategy["id"], "1.0.0", VALID_DSL).json()
    pending = _stored_version(
        db_session, strategy["id"], version="2.0.0", status="pending", is_current=False
    )

    refused = client.put(f"/api/v1/strategy-versions/{pending.id}/activate")

    assert refused.status_code == 422
    assert refused.json()["detail"] == NOT_VALID.format(status="pending")
    listed = {row["id"]: row for row in _versions(client, strategy["id"])}
    assert listed[pending.id]["is_current"] is False
    assert listed[current["id"]]["is_current"] is True


def test_activating_a_valid_version_still_succeeds(client) -> None:
    """The compatibility half of the rule: today's flow is untouched."""

    strategy = _strategy(client, "Gate valid activate")
    first = _create_version(client, strategy["id"], "1.0.0", VALID_DSL).json()
    second = _create_version(client, strategy["id"], "1.1.0", VALID_DSL).json()

    activated = client.put(f"/api/v1/strategy-versions/{first['id']}/activate")

    assert activated.status_code == 200
    assert activated.json()["is_current"] is True
    assert client.get(f"/api/v1/strategy-versions/{second['id']}").json()["is_current"] is False


def test_a_refused_activation_is_audited(client) -> None:
    strategy = _strategy(client, "Gate audited refusal")
    invalid = _create_version(
        client, strategy["id"], "1.1.0", INVALID_DSL, make_current=False
    ).json()
    client.put(f"/api/v1/strategy-versions/{invalid['id']}/activate")

    events = client.get("/api/v1/audit/logs").json()["events"]
    refusals = [e for e in events if e["event_type"] == "strategy_version_activation_rejected"]

    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal["entity_type"] == "strategy_version"
    assert refusal["entity_id"] == str(invalid["id"])
    assert refusal["action"] == "reject"
    assert refusal["payload"] == {
        "strategy_id": strategy["id"],
        "version": "1.1.0",
        "validation_status": "invalid",
        "reason": "validation_status_not_valid",
    }


# --- 3. the create endpoint (make_current) ------------------------------------------


def test_make_current_on_an_invalid_version_is_refused_and_writes_nothing(client) -> None:
    strategy = _strategy(client, "Gate create invalid current")
    _create_version(client, strategy["id"], "1.0.0", VALID_DSL)

    # `make_current` is omitted on purpose: the server default is true (schemas.py:165)
    # and that default is exactly how an invalid version reached `is_current`.
    refused = _create_version(client, strategy["id"], "1.1.0", INVALID_DSL)

    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        NOT_VALID.format(status="invalid")
        + "; pass make_current=false to record it without making it current"
    )
    listed = _versions(client, strategy["id"])
    assert [row["version"] for row in listed] == ["1.0.0"]
    assert listed[0]["is_current"] is True


def test_recording_an_invalid_version_without_current_still_works(client) -> None:
    """The escape hatch: an invalid version may be recorded, it may not be activated."""

    strategy = _strategy(client, "Gate record invalid")

    recorded = _create_version(client, strategy["id"], "1.0.0", INVALID_DSL, make_current=False)

    assert recorded.status_code == 201
    body = recorded.json()
    assert body["validation_status"] == "invalid"
    assert body["is_current"] is False


# --- 4. the scanner -----------------------------------------------------------------


def _seed_series(db, *, symbol: str, slug: str, status: str, bars):
    """Strategy + version + asset + source + series + bars, with a chosen status."""

    from app.data.market_data_repo import frame_to_bars, upsert_bars

    strategy = Strategy(name=f"S-{symbol}", slug=slug)
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json=VALID_DSL,
        immutable_hash="s" * 64,
        validation_status=status,
        is_current=True,
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol=symbol, asset_class="stock")
    db.add(asset)
    db.flush()
    source = MarketDataSource(name=f"src-{slug}", base_url="x")
    db.add(source)
    db.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db.add(series)
    db.flush()
    upsert_bars(db, series, frame_to_bars(bars))
    db.commit()
    return version, series


def test_the_scanner_never_scans_a_current_version_that_is_not_valid(
    db_session, sample_bars
) -> None:
    invalid, _invalid_series = _seed_series(
        db_session, symbol="GATE-BAD", slug="gate-bad", status="invalid", bars=sample_bars
    )
    valid, _valid_series = _seed_series(
        db_session, symbol="GATE-OK", slug="gate-ok", status="valid", bars=sample_bars
    )

    scanned = {(row["strategy_version_id"], row["symbol"]) for row in scan_all(db_session)}

    # The control comes first: without it, "the invalid one is missing" could just
    # mean the scan never produced anything at all.
    assert (valid.id, "GATE-OK") in scanned
    assert not [item for item in scanned if item[0] == invalid.id]
