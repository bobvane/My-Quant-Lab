"""A delete must count every row that points at it (ADR-083).

Regression, in the same family as ADR-081: ``DELETE /strategies/{id}`` asked
only whether a *backtest* referenced the strategy. Two other tables reference
it -- signals point at a version, paper accounts point at the strategy -- and
none of those columns carries ``ON DELETE``, so the delete reached the database,
hit a foreign-key violation and reached the user as a generic 500. The guard has
to count what points at the row, not the one referrer we happened to remember.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas import MarketDataSyncRequest, StrategyVersionCreate
from app.data.ai_provider_service import ProviderConfigError, create_provider, delete_provider
from app.domain.models import AIModel, AIUsage, Asset, PaperAccount, Signal

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "delete-referrers", "name": "Delete Referrers", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _strategy_with_version(client, name: str) -> tuple[int, int]:
    strategy = client.post("/api/v1/strategies", json={"name": name}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json=StrategyVersionCreate(version="1.0.0", dsl=DSL).model_dump(),
    ).json()
    return strategy["id"], version["id"]


def _asset_id(db_session) -> int:
    asset = db_session.scalar(select(Asset).where(Asset.symbol == "DEMO-AAPL"))
    assert asset is not None, "the test forgot to sync DEMO-AAPL first"
    return asset.id


def _signal(db_session, version_id: int, day: int) -> Signal:
    return Signal(
        strategy_version_id=version_id,
        asset_id=_asset_id(db_session),
        timeframe="1d",
        bar_timestamp=dt.datetime(2024, 1, day, tzinfo=dt.UTC),
        state="new",
        direction="LONG",
        feature_snapshot_hash="0" * 64,
        data_source="synthetic",
    )


def test_a_signal_blocks_deleting_its_strategy(client, db_session) -> None:
    """A signal pointing at a version keeps the strategy (the 500 this fixed)."""

    client.post(
        "/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol="DEMO-AAPL").model_dump()
    )
    strategy_id, version_id = _strategy_with_version(client, "SignalsOnly")
    db_session.add(_signal(db_session, version_id, 2))
    db_session.flush()

    response = client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 409, response.text
    assert "信号记录" in response.json()["detail"]
    assert client.get(f"/api/v1/strategies/{strategy_id}").status_code == 200


def test_a_paper_account_blocks_deleting_its_strategy(client, db_session) -> None:
    """Deleting the strategy would leave the simulated account pointing at nothing."""

    strategy_id, _ = _strategy_with_version(client, "PaperBacked")
    db_session.add(
        PaperAccount(
            name="Simulated",
            strategy_id=strategy_id,
            initial_cash=Decimal("1000"),
            cash=Decimal("1000"),
        )
    )
    db_session.flush()

    response = client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 409, response.text
    assert "模拟盘账户" in response.json()["detail"]


def test_the_refusal_counts_every_referrer(client, db_session) -> None:
    """The message says how many rows of which kind hold it, not just "in use"."""

    client.post(
        "/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol="DEMO-AAPL").model_dump()
    )
    strategy_id, version_id = _strategy_with_version(client, "Crowded")
    db_session.add_all([_signal(db_session, version_id, 2), _signal(db_session, version_id, 3)])
    db_session.add(
        PaperAccount(
            name="Simulated",
            strategy_id=strategy_id,
            initial_cash=Decimal("1000"),
            cash=Decimal("1000"),
        )
    )
    db_session.flush()

    detail = client.delete(f"/api/v1/strategies/{strategy_id}").json()["detail"]

    assert "2 条信号记录" in detail
    assert "1 条模拟盘账户" in detail


def test_a_strategy_nobody_points_at_still_deletes(client, db_session) -> None:
    """The guard must not become "refuse everything": a version alone is fine."""

    strategy_id, _ = _strategy_with_version(client, "Unreferenced")

    response = client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/strategies/{strategy_id}").status_code == 404


def test_ai_usage_blocks_deleting_its_provider(db_session) -> None:
    """A usage row without an AI task is still history (the old guard asked tasks only)."""

    provider = create_provider(
        db_session,
        name="svc",
        base_url="https://api.example.com/v1",
        api_key="sk-abc",
        default_model="m1",
        daily_budget_usd=1.0,
    )
    db_session.add(
        AIUsage(
            usage_date=dt.date(2026, 1, 1),
            provider_id=provider.id,
            task_type="explain",
            call_count=1,
            total_tokens=10,
            total_cost_usd=Decimal("0.001"),
        )
    )
    db_session.flush()

    with pytest.raises(ProviderConfigError, match="usage history"):
        delete_provider(db_session, provider.id)


def test_ai_usage_of_a_model_alone_blocks_the_provider(db_session) -> None:
    """The provider's models cascade away with it, so their usage blocks it too."""

    provider = create_provider(
        db_session,
        name="svc",
        base_url="https://api.example.com/v1",
        api_key="sk-abc",
        default_model="m1",
        daily_budget_usd=1.0,
    )
    model = db_session.scalar(select(AIModel).where(AIModel.provider_id == provider.id))
    assert model is not None, "creating a provider registers its default model"
    db_session.add(
        AIUsage(
            usage_date=dt.date(2026, 1, 1),
            model_id=model.id,
            task_type="explain",
            call_count=1,
            total_tokens=10,
            total_cost_usd=Decimal("0.001"),
        )
    )
    db_session.flush()

    with pytest.raises(ProviderConfigError, match="usage history"):
        delete_provider(db_session, provider.id)
