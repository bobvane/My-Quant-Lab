"""Monte Carlo API tests (docs/22, ADR-043).

Endpoint contract: it resamples a *stored* backtest (never re-runs it), refuses runs
that cannot support a distribution, and records an audit event.
"""

from __future__ import annotations

import copy

from app.api.schemas import MarketDataSyncRequest

_SYMBOL = "DEMO-AAPL"

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "mc-api", "name": "MC API", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
}


def _completed_run(client) -> int:
    """Sync data, create a strategy version and run a backtest; return the run id."""

    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    strategy = client.post("/api/v1/strategies", json={"name": "Monte Carlo"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(_DSL)},
    ).json()
    run = client.post(
        "/api/v1/backtests",
        json={"strategy_version_id": version["id"], "symbol": _SYMBOL, "timeframe": "1d"},
    ).json()
    return int(run["id"])


def test_monte_carlo_endpoint_returns_a_distribution(client) -> None:
    run_id = _completed_run(client)

    response = client.post(
        "/api/v1/research/monte-carlo",
        json={"backtest_run_id": run_id, "runs": 200, "seed": 7},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["method"] == "trade_level_iid_bootstrap"
    assert body["seed"] == 7
    assert body["monte_carlo_version"]

    summary = body["summary"]
    assert summary["runs"] == 200
    assert summary["observed_trades"] > 0
    assert 0.0 <= summary["probability_of_profit"] <= 1.0
    assert 0.0 <= summary["probability_of_ruin"] <= 1.0
    # Percentiles must be present and ordered.
    for key in ("total_return", "max_drawdown", "final_equity"):
        p = summary[key]
        assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"], key
    assert body["sample_equity_paths"]


def test_monte_carlo_is_reproducible_over_the_api(client) -> None:
    run_id = _completed_run(client)
    payload = {"backtest_run_id": run_id, "runs": 150, "seed": 99}

    first = client.post("/api/v1/research/monte-carlo", json=payload).json()
    second = client.post("/api/v1/research/monte-carlo", json=payload).json()
    assert first == second


def test_monte_carlo_defaults_are_applied(client) -> None:
    run_id = _completed_run(client)
    body = client.post(
        "/api/v1/research/monte-carlo", json={"backtest_run_id": run_id, "runs": 50}
    ).json()
    assert body["seed"] == 0
    assert body["summary"]["runs"] == 50


def test_monte_carlo_unknown_run_is_404(client) -> None:
    response = client.post(
        "/api/v1/research/monte-carlo", json={"backtest_run_id": 999_999, "runs": 10}
    )
    assert response.status_code == 404, response.text


def test_monte_carlo_rejects_oversized_runs(client) -> None:
    run_id = _completed_run(client)
    response = client.post(
        "/api/v1/research/monte-carlo", json={"backtest_run_id": run_id, "runs": 99_999}
    )
    # Pydantic's le=5000 rejects it before the engine is reached.
    assert response.status_code == 422, response.text


def test_monte_carlo_rejects_zero_runs(client) -> None:
    run_id = _completed_run(client)
    response = client.post(
        "/api/v1/research/monte-carlo", json={"backtest_run_id": run_id, "runs": 0}
    )
    assert response.status_code == 422, response.text


def test_monte_carlo_records_an_audit_event(client, db_session) -> None:
    from sqlalchemy import select

    from app.domain.models import AuditLog

    run_id = _completed_run(client)
    response = client.post(
        "/api/v1/research/monte-carlo",
        json={"backtest_run_id": run_id, "runs": 120, "seed": 3},
    )
    assert response.status_code == 200, response.text

    events = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type == "monte_carlo_completed")
    ).all()
    assert len(events) == 1
    payload = events[0].payload_json
    assert payload["runs"] == 120
    assert payload["seed"] == 3
    assert payload["method"] == "trade_level_iid_bootstrap"
    assert payload["observed_trades"] > 0


def test_monte_carlo_trades_per_run_scales_horizon(client) -> None:
    run_id = _completed_run(client)
    body = client.post(
        "/api/v1/research/monte-carlo",
        json={"backtest_run_id": run_id, "runs": 100, "trades_per_run": 25, "seed": 1},
    ).json()
    assert body["summary"]["trades_per_run"] == 25
