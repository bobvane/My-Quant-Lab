"""Ensemble vote-threshold sweep API tests (docs/24 §7, ADR-052).

The sweep is only useful if it describes the *same* ensemble the single-threshold
endpoint produces. The central test here is therefore the agreement check between
``POST /research/ensemble/sweep`` and ``POST /research/ensemble`` at one threshold:
if a sweep point could diverge from a direct run, the chart would show the user a
staircase they cannot reproduce.
"""

from __future__ import annotations

import copy

import pytest

from app.api.schemas import MarketDataSyncRequest

_SYMBOL = "DEMO-AAPL"


def _dsl(strategy_id: str, fast: int, slow: int) -> dict:
    return {
        "schema_version": "1.0",
        "strategy": {"id": strategy_id, "name": strategy_id, "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "indicators": [
            {"id": "ema_fast", "type": "EMA", "period_ref": "fast_period"},
            {"id": "ema_slow", "type": "EMA", "period_ref": "slow_period"},
        ],
        "parameters": {"fast_period": fast, "slow_period": slow},
        "entry": {
            "long": {"all": [{"op": "crosses_above", "left": "ema_fast", "right": "ema_slow"}]}
        },
        "exit": {
            "long": {"any": [{"op": "crosses_below", "left": "ema_fast", "right": "ema_slow"}]}
        },
        "risk": {"stop_loss_atr_multiple": 2.0, "max_position_pct": 0.5},
        "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
    }


def _version(client, strategy_id: str, fast: int, slow: int) -> int:
    strategy = client.post("/api/v1/strategies", json={"name": strategy_id}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(_dsl(strategy_id, fast, slow))},
    ).json()
    return int(version["id"])


def _seed(client) -> tuple[int, int]:
    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    return _version(client, "sweep-a", 5, 20), _version(client, "sweep-b", 10, 30)


def _members(a: int, b: int) -> list[dict]:
    return [
        {"strategy_version_id": a, "weight": 1.0},
        {"strategy_version_id": b, "weight": 1.0},
    ]


def test_sweep_endpoint_matches_the_single_threshold_endpoint(client) -> None:
    a, b = _seed(client)

    direct = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": _members(a, b),
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "vote_threshold": 0.5,
        },
    )
    assert direct.status_code == 200, direct.text
    single = direct.json()

    swept = client.post(
        "/api/v1/research/ensemble/sweep",
        json={
            "members": _members(a, b),
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "thresholds": [0.5],
        },
    )
    assert swept.status_code == 200, swept.text
    body = swept.json()

    assert body["engine_version"] == single["engine_version"] == "ensemble-1.2.0"
    assert body["bars_evaluated"] == single["bars_evaluated"]
    assert body["initial_capital"] == single["initial_capital"]
    assert body["dataset_version_id"] == single["dataset_version_id"]
    assert body["symbol"] == single["symbol"] == _SYMBOL
    assert body["timeframe"] == single["timeframe"]

    assert len(body["points"]) == 1
    point = body["points"][0]
    assert point["vote_threshold"] == 0.5
    # Same arithmetic, so every number the sweep reports must equal the direct run's.
    assert point["entries_taken"] == single["agreement"]["entries_taken"]
    assert point["entry_bars"] == single["agreement"]["entry_bars"]
    assert point["signalled_bars"] == single["agreement"]["signalled_bars"]
    assert point["solo_signalled_bars"] == single["agreement"]["solo_signalled_bars"]
    assert point["final_equity"] == pytest.approx(single["final_equity"])
    assert point["total_return"] == pytest.approx(single["metrics"]["total_return"])
    assert point["max_drawdown"] == pytest.approx(single["metrics"]["max_drawdown"])
    assert point["number_of_trades"] == single["metrics"]["number_of_trades"]
    # A sweep is a report about thresholds, not a portfolio: no curves in the contract.
    assert "equity_curve" not in body
    assert "member_runs" not in body


def test_default_thresholds_are_the_coalition_totals(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={"members": _members(a, b), "symbol": _SYMBOL, "timeframe": "1d"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Equal weights can only produce 0, 0.5 or 1.0 of the vote, so only 0.0 and 0.5
    # are informative thresholds; anything between them repeats a neighbour.
    assert body["thresholds"] == [0.0, 0.5]
    assert body["possible_votes"] == [0.0, 0.5, 1.0]
    assert [p["vote_threshold"] for p in body["points"]] == body["thresholds"]
    # 0.0 is clearable by a single member (0.5 > 0.0); 0.5 can only be cleared by both
    # agreeing (1.0 > 0.5). This is the whole point of `effective_vote`: it names the
    # coalition the threshold is actually waiting for.
    assert [p["effective_vote"] for p in body["points"]] == [0.5, 1.0]


def test_sweep_reports_member_shares(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={"members": _members(a, b), "symbol": _SYMBOL, "timeframe": "1d"},
    )
    body = response.json()
    assert [m["weight"] for m in body["members"]] == [0.5, 0.5]
    assert [m["weight_share"] for m in body["members"]] == [0.5, 0.5]
    assert len(body["members"]) == 2


def test_a_higher_threshold_never_opens_more_positions(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={
            "members": _members(a, b),
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "thresholds": [0.0, 0.25, 0.5, 0.75],
        },
    )
    assert response.status_code == 200, response.text
    points = response.json()["points"]

    entries = [p["entries_taken"] for p in points]
    entry_bars = [p["entry_bars"] for p in points]
    # Raising the bar can only remove trades, never add them. A non-monotone series
    # would mean a sweep point used different bars or a different decision merge.
    assert entries == sorted(entries, reverse=True)
    assert entry_bars == sorted(entry_bars, reverse=True)
    # 0.75 leaves only the full coalition (1.0 > 0.75), so every entry needs both
    # members on the same bar — a strict subset of what the lowest threshold allows.
    assert [p["effective_vote"] for p in points] == [0.5, 0.5, 1.0, 1.0]
    assert entries[0] >= entries[-1]
    assert entry_bars[-1] <= entry_bars[0]


def test_sweep_records_an_audit_event(client, db_session) -> None:
    from sqlalchemy import select

    from app.domain.models import AuditLog

    a, b = _seed(client)
    client.post(
        "/api/v1/research/ensemble/sweep",
        json={"members": _members(a, b), "symbol": _SYMBOL, "timeframe": "1d"},
    )
    events = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type == "ensemble_sweep_completed")
    ).all()
    assert len(events) == 1
    payload = events[0].payload_json
    assert payload["bars_evaluated"] > 0
    assert payload["thresholds"] == [0.0, 0.5]
    assert len(payload["entries_taken"]) == 2
    assert len(payload["members"]) == 2


def test_sweep_rejects_duplicate_members(client) -> None:
    a, _ = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={
            "members": [
                {"strategy_version_id": a, "weight": 1.0},
                {"strategy_version_id": a, "weight": 1.0},
            ],
            "symbol": _SYMBOL,
            "timeframe": "1d",
        },
    )
    assert response.status_code == 422
    assert "duplicate members" in response.json()["detail"]


def test_sweep_rejects_an_out_of_range_threshold(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={
            "members": _members(a, b),
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "thresholds": [0.5, 1.0],
        },
    )
    assert response.status_code == 422


def test_sweep_rejects_an_empty_threshold_list(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={
            "members": _members(a, b),
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "thresholds": [],
        },
    )
    assert response.status_code == 422


def test_sweep_unknown_member_is_404(client) -> None:
    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    response = client.post(
        "/api/v1/research/ensemble/sweep",
        json={
            "members": [{"strategy_version_id": 999_999, "weight": 1.0}],
            "symbol": _SYMBOL,
            "timeframe": "1d",
        },
    )
    assert response.status_code == 404
