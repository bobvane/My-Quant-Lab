"""Out-of-sample holdout split (docs/07 §11)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.research.walk_forward import run_holdout
from app.strategies.dsl import StrategySpec

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "oos", "name": "OOS", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def test_holdout_by_percentage(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(_DSL)
    outcome = run_holdout(spec, sample_bars, oos_pct=0.25)
    assert outcome["in_sample_bars"] == 300
    assert outcome["out_of_sample_bars"] == 100
    assert "total_return" in outcome["in_sample"]
    assert "total_return" in outcome["out_of_sample"]
    assert pd.Timestamp(outcome["split_time"]).tzinfo is not None


def test_holdout_by_start_date(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(_DSL)
    outcome = run_holdout(spec, sample_bars, oos_start="2024-01-01")
    assert outcome["in_sample_bars"] > 0
    assert outcome["out_of_sample_bars"] > 0
    assert outcome["split_time"].startswith("2024-01-")


def test_holdout_defaults_to_last_20_percent(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(_DSL)
    outcome = run_holdout(spec, sample_bars)
    assert outcome["out_of_sample_bars"] == 80


def test_holdout_rejects_bad_percentage(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(_DSL)
    with pytest.raises(ValueError):
        run_holdout(spec, sample_bars, oos_pct=0.0)
    with pytest.raises(ValueError):
        run_holdout(spec, sample_bars, oos_pct=1.0)


def test_holdout_rejects_split_outside_range(sample_bars: pd.DataFrame) -> None:
    spec = StrategySpec.model_validate(_DSL)
    with pytest.raises(ValueError):
        run_holdout(spec, sample_bars, oos_start="2099-01-01")


def test_oos_api_endpoint(client) -> None:
    from app.api.schemas import MarketDataSyncRequest

    # Sync synthetic data, create a strategy + version, then request an OOS split.
    client.post(
        "/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol="DEMO-AAPL").model_dump()
    )
    strategy = client.post("/api/v1/strategies", json={"name": "OOS API"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": _DSL},
    ).json()

    response = client.post(
        "/api/v1/research/oos",
        json={
            "strategy_version_id": version["id"],
            "symbol": "DEMO-AAPL",
            "timeframe": "1d",
            "oos_pct": 0.3,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["in_sample_bars"] > 0
    assert body["out_of_sample_bars"] > 0
    assert "total_return" in body["out_of_sample"]
