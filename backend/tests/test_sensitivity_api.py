"""Parameter sensitivity API tests (docs/21, ADR-040).

Covers the endpoint contract: a real sweep, the 422s for caller errors, and the
audit record that makes a sweep reproducible after the fact.
"""

from __future__ import annotations

import copy

import pytest

from app.api.schemas import MarketDataSyncRequest

_SYMBOL = "DEMO-AAPL"

# Parameterised through `period_ref`, so the sweep has something to move. The
# indicator id doubles as the materialised column the rules reference.
_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "sens-api", "name": "Sens API", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "indicators": [{"id": "trend", "type": "EMA", "period_ref": "trend_period"}],
    "parameters": {"trend_period": 20},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "trend"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "trend"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
}

# A DSL with no `parameters` at all: any grid axis is unknown for it.
_UNPARAMETERISED_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "plain", "name": "Plain", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5},
}


def _seed(client, dsl: dict) -> int:
    """Sync data and create a strategy version; return its id."""

    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    strategy = client.post("/api/v1/strategies", json={"name": "Sensitivity"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(dsl)},
    ).json()
    return int(version["id"])


def test_sensitivity_endpoint_sweeps_the_grid(client) -> None:
    version_id = _seed(client, _DSL)

    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "grid": {"trend_period": [10, 20, 40]},
            "metric": "sharpe",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["metric"] == "sharpe"
    assert body["grid_points"] == 3
    assert body["axes"] == {"trend_period": [10, 20, 40]}
    assert [p["parameters"]["trend_period"] for p in body["points"]] == [10, 20, 40]
    # The sweep must not collapse: distinct parameters, distinct computations.
    assert len({p["result_hash"] for p in body["points"]}) == 3
    assert "mean" in body["summary"]
    assert body["stable"] in (True, False, None)


def test_sensitivity_endpoint_defaults_metric_to_sharpe(client) -> None:
    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [20]},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["metric"] == "sharpe"


def test_sensitivity_rejects_unknown_axis(client) -> None:
    version_id = _seed(client, _UNPARAMETERISED_DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [10, 20]},
        },
    )
    assert response.status_code == 422, response.text
    assert "not declared by the strategy" in response.json()["detail"]


def test_sensitivity_rejects_oversized_grid(client) -> None:
    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": list(range(1, 400))},
        },
    )
    assert response.status_code == 422, response.text
    assert "maximum" in response.json()["detail"]


def test_sensitivity_rejects_unsupported_metric(client) -> None:
    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [20]},
            "metric": "magic",
        },
    )
    assert response.status_code == 422, response.text
    assert "unsupported metric" in response.json()["detail"]


def test_sensitivity_requires_a_non_empty_grid(client) -> None:
    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={"strategy_version_id": version_id, "symbol": _SYMBOL, "grid": {}},
    )
    # Pydantic enforces min_length=1 on the grid before the engine is reached.
    assert response.status_code == 422, response.text


def test_sensitivity_unknown_strategy_version_is_404(client) -> None:
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": 999_999,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [20]},
        },
    )
    assert response.status_code == 404, response.text


def test_sensitivity_missing_series_is_404(client) -> None:
    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": "NO-SUCH-SYMBOL",
            "grid": {"trend_period": [20]},
        },
    )
    assert response.status_code == 404, response.text


def test_sensitivity_records_an_audit_event(client, db_session) -> None:
    from sqlalchemy import select

    from app.domain.models import AuditLog

    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [10, 20]},
        },
    )
    assert response.status_code == 200, response.text

    events = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type == "sensitivity_completed")
    ).all()
    assert len(events) == 1
    payload = events[0].payload_json
    assert payload["grid_points"] == 2
    assert payload["metric"] == "sharpe"
    assert payload["axes"] == {"trend_period": [10, 20]}


@pytest.mark.parametrize("metric", ["total_return", "max_drawdown", "win_rate"])
def test_sensitivity_supports_other_metrics(client, metric: str) -> None:
    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [10, 20]},
            "metric": metric,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["metric"] == metric


def test_points_that_never_ran_cannot_win_the_ranking(client) -> None:
    """A grid point whose window sat inside the warm-up must not be a result (ADR-055).

    Such a point never gets an evaluable bar, so it reports the flat 0.0 of a strategy
    that never traded. Ranking that against points that genuinely lost money makes "did
    not run" look like the best outcome, and its zero breaks the sign-consistency check.
    """

    version_id = _seed(client, _DSL)
    response = client.post(
        "/api/v1/research/sensitivity",
        json={
            "strategy_version_id": version_id,
            "symbol": _SYMBOL,
            "grid": {"trend_period": [100, 300, 500, 900]},
            "metric": "total_return",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    by_period = {p["parameters"]["trend_period"]: p for p in body["points"]}
    starved = [period for period, p in by_period.items() if p["warmup_unmet"]]
    assert starved == [500, 900]

    # Counted as evaluated (their objective is defined, it is just not a measurement)...
    assert body["evaluated_points"] == 4
    # ...but only two points are evidence of anything.
    assert body["ranked_points"] == 2
    assert body["warmup_unmet_points"] == 2
    assert body["warnings"], body
    assert "warm-up" in body["warnings"][0]

    # The trap is real: the points that never ran do report a non-negative objective.
    for period in starved:
        assert by_period[period]["objective"] == 0.0
        assert by_period[period]["metrics"]["number_of_trades"] == 0

    # Every measured point lost money, so under the old rule a 0.0 was the winner.
    measured = {period: p for period, p in by_period.items() if not p["warmup_unmet"]}
    assert all(p["objective"] < 0 for p in measured.values())
    assert body["best"]["parameters"]["trend_period"] == 100
    assert body["worst"]["parameters"]["trend_period"] == 300
    assert body["best"]["parameters"]["trend_period"] not in starved
    assert body["worst"]["parameters"]["trend_period"] not in starved

    # The same zeros used to read as "the objective flips sign across the grid".
    assert body["stable"] is True
