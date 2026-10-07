"""Read endpoints for features / AI models / prompts / backtest metrics (docs/12)."""

from __future__ import annotations

from app.domain.models import BacktestResult, BacktestRun, Strategy, StrategyVersion


def test_feature_catalogue_and_ai_registries(client) -> None:
    # The catalogue comes from code (ADR-093): it is what the engine computes, not a
    # table nobody ever wrote to.
    features = client.get("/api/v1/features").json()
    assert features, "the engine computes features, so the catalogue cannot be empty"
    names = {f["name"] for f in features}
    assert {"ema20", "rsi14", "breakout"} <= names
    assert all(f["feature_version"] for f in features)

    assert client.get("/api/v1/ai/models").json()["models"] == []
    # Built-in prompt definitions are registered on first read (docs/06) and come
    # from the role contract files (ADR-150): the catalogue is exactly the
    # contracts' tasks, with nothing spelled out in Python and nothing missing.
    from app.ai.role_contracts import role_contracts

    prompts = client.get("/api/v1/ai/prompts").json()["prompts"]
    expected = {
        (contract.prompt_name_for(task_type), contract.version)
        for contract in role_contracts()
        for task_type in contract.task_types
    }
    assert {(p["name"], p["version"]) for p in prompts} == expected
    # The explanation prompts keep the names the rest of the code calls them by.
    assert {p["name"] for p in prompts if p["name"].endswith("_explain")} == {
        "signal_explain",
        "backtest_explain",
        "performance_explain",
    }


def test_backtest_metrics_endpoint_404(client) -> None:
    assert client.get("/api/v1/backtest-metrics/backtest/9999").status_code == 404


def test_feature_versions_endpoint(client) -> None:
    body = client.get("/api/v1/features/versions").json()
    assert body["engine_feature_version"]
    assert body["indicator_version"] and body["price_action_version"]
    assert isinstance(body["snapshot_versions"], list)
    # The dead table the endpoint used to read is gone, and so is the key it fed.
    assert "definition_versions" not in body


def test_data_sources_and_lineage(client) -> None:
    sources = client.get("/api/v1/market-data/data-sources").json()
    assert isinstance(sources, list)

    strategy = client.post(
        "/api/v1/strategies",
        json={"name": "Lineage", "source_url": "https://github.com/x/y"},
    ).json()
    client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": _DSL_FOR_LINEAGE},
    )
    lineage = client.get(f"/api/v1/strategies/{strategy['id']}/lineage").json()
    assert lineage["name"] == "Lineage"
    assert lineage["source_url"] == "https://github.com/x/y"
    assert len(lineage["versions"]) == 1
    assert lineage["versions"][0]["immutable_hash"]
    assert client.get("/api/v1/strategies/9999/lineage").status_code == 404


_DSL_FOR_LINEAGE = {
    "schema_version": "1.0",
    "strategy": {"id": "ln", "name": "LN", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def test_compare_backtests(client, db_session) -> None:
    from app.domain.models import Asset, MarketDataSeries, MarketDataSource

    strategy = Strategy(name="Cmp", slug="cmp-strat")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="c" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="CMP", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="cmp-src", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    run_ids = []
    for ret in (0.05, 0.10):
        run = BacktestRun(
            strategy_version_id=version.id,
            dataset_version_id=series.id,
            dataset_hash="d" * 64,
            status="completed",
        )
        db_session.add(run)
        db_session.flush()
        db_session.add(
            BacktestResult(
                backtest_run_id=run.id,
                summary_json={"total_return": ret, "number_of_trades": 3},
                metrics_json={},
                result_hash="r" * 64,
            )
        )
        run_ids.append(run.id)
    db_session.commit()

    body = client.get(f"/api/v1/backtests/compare?ids={run_ids[0]},{run_ids[1]}").json()
    assert len(body["runs"]) == 2
    assert {r["total_return"] for r in body["runs"]} == {0.05, 0.10}

    assert client.get(f"/api/v1/backtests/compare?ids={run_ids[0]}").status_code == 422


def test_backtest_metrics_returns_result(client, db_session) -> None:
    from app.domain.models import (
        Asset,
        MarketDataSeries,
        MarketDataSource,
    )

    strategy = Strategy(name="Metrics", slug="metrics-strat")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="m" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="MTA", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="metrics-src", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        dataset_hash="d" * 64,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        BacktestResult(
            backtest_run_id=run.id,
            summary_json={"total_return": 0.12, "number_of_trades": 4},
            metrics_json={"sharpe": 1.3, "max_drawdown": -0.1},
            result_hash="r" * 64,
        )
    )
    db_session.commit()

    body = client.get(f"/api/v1/backtest-metrics/backtest/{run.id}").json()
    assert body["metrics"]["sharpe"] == 1.3
    assert body["summary"]["number_of_trades"] == 4
