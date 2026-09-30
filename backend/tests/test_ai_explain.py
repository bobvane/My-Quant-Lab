"""Tests for the AI explanation layer.

A fake router stands in for the LLM: no test in this file touches the network.
The tests pin down the safety properties — caching, budget enforcement,
schema validation, unconfigured behaviour — not model eloquence.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from app.ai.explain import (
    AI_UNCONFIGURED,
    build_backtest_facts,
    build_signal_facts,
    explain_backtest,
    explain_signal,
    explain_signal_facts,
    get_active_provider,
)
from app.ai.provider import BudgetExceeded
from app.domain.models import (
    AIProvider,
    Asset,
    BacktestResult,
    BacktestRun,
    MarketDataSeries,
    MarketDataSource,
    Signal,
    Strategy,
    StrategyVersion,
)
from app.infrastructure.secrets import encrypt_secret

CANNED_SIGNAL_EXPLANATION = {
    "summary": "价格上穿均线，触发买入信号。",
    "why": ["收盘价高于 EMA20", "快慢线金叉确认趋势"],
    "what_could_invalidate": ["收盘跌回 EMA20 下方"],
    "what_to_watch_next": ["下一根 K 线能否站稳"],
    "risk_notes": ["震荡市容易假突破"],
    "plain_language": "简单说：趋势刚刚转强，但还没走稳。",
}

CANNED_BACKTEST_EXPLANATION = {
    "summary": "回测显示策略整体微利但波动不小。",
    "key_drivers": ["少数几笔大盈利贡献了主要收益"],
    "risks": ["最大回撤期间连续亏损"],
    "what_to_watch_next": ["样本外表现是否一致"],
    "plain_language": "简单说：能赚钱但过程颠簸，别重仓。",
}


class FakeRouter:
    """Test double with the same shape as AIRouter.explain_signal."""

    def __init__(self, output: dict[str, Any], calls: list) -> None:
        self._output = output
        self.calls = calls

    def explain_signal(self, request, *, spent_today_usd: float = 0.0) -> dict[str, Any]:
        self.calls.append(request)
        if spent_today_usd >= 10**9:
            raise BudgetExceeded("test budget")
        return dict(self._output)


def make_factory(output: dict[str, Any], calls: list):
    """Build a router_factory returning a FakeRouter with canned output."""

    def factory(providers: dict, budget: float) -> FakeRouter:
        return FakeRouter(output, calls)

    return factory


def _provider(db_session, *, budget: float = 2.0) -> AIProvider:
    provider = AIProvider(
        name="test-openai",
        provider_type="openai_compatible",
        base_url="http://localhost:9",
        api_key_encrypted=encrypt_secret("sk-test"),
        default_model="test-model",
        daily_budget_usd=budget,
    )
    db_session.add(provider)
    db_session.flush()
    return provider


def _facts() -> dict[str, Any]:
    return {
        "kind": "signal",
        "state": "BUY",
        "direction": "LONG",
        "symbol": "DEMO",
        "timeframe": "1d",
        "bar_time": "2026-01-01T00:00:00+00:00",
        "price_reference": 100.0,
        "triggered_rules": ["crosses_above:ema20:ema50"],
        "strategy_version": "1.0.0",
        "data_source": "series:1/v1",
    }


def test_unconfigured_provider_raises_clear_error(db_session) -> None:
    with pytest.raises(RuntimeError, match=AI_UNCONFIGURED):
        explain_signal_facts(db_session, _facts())
    assert get_active_provider(db_session) is None


def test_explain_uses_cache_on_second_call(db_session) -> None:
    _provider(db_session)
    calls: list = []
    factory = make_factory(CANNED_SIGNAL_EXPLANATION, calls)

    first = explain_signal_facts(db_session, _facts(), router_factory=factory)
    assert first["cached"] is False
    assert first["explanation"]["summary"].startswith("价格上穿")
    assert len(calls) == 1

    second = explain_signal_facts(db_session, _facts(), router_factory=factory)
    assert second["cached"] is True
    assert second["task_id"] == first["task_id"]
    assert len(calls) == 1  # provider NOT called again
    assert second["cost_usd_estimated"] == 0.0


def test_budget_exceeded_blocks_call(db_session) -> None:
    _provider(db_session, budget=0.0)
    calls: list = []
    factory = make_factory(CANNED_SIGNAL_EXPLANATION, calls)
    # Zero budget means even the first call is refused; spent(0) >= budget(0).
    with pytest.raises(BudgetExceeded):
        explain_signal_facts(db_session, _facts(), router_factory=factory)
    assert calls == []


def test_bad_model_output_marks_task_failed(db_session) -> None:
    _provider(db_session)
    calls: list = []
    factory = make_factory({"wrong": "shape"}, calls)
    with pytest.raises(RuntimeError, match="AI provider call failed"):
        explain_signal_facts(db_session, _facts(), router_factory=factory)

    from sqlalchemy import select

    from app.domain.models import AITask

    task = db_session.scalar(select(AITask).order_by(AITask.id.desc()))
    assert task is not None and task.status == "failed"
    assert "missing required field" in (task.error_message or "")


def test_usage_is_recorded(db_session) -> None:
    _provider(db_session)
    calls: list = []
    factory = make_factory(CANNED_SIGNAL_EXPLANATION, calls)
    explain_signal_facts(db_session, _facts(), router_factory=factory)

    from sqlalchemy import select

    from app.domain.models import AITask, AIUsage

    task = db_session.scalar(select(AITask).order_by(AITask.id.desc()))
    assert task is not None and task.status == "completed"
    assert task.token_usage_json and task.token_usage_json.get("estimated") is True
    usage = db_session.scalars(select(AIUsage)).all()
    assert len(usage) == 1 and usage[0].call_count == 1


def _seed_signal(db_session) -> int:
    strategy = Strategy(name="AI Sig", slug="ai-sig")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={},
        immutable_hash="x" * 64,
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="AIDEMO")
    db_session.add(asset)
    db_session.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=Decimal("101.5"),
        triggered_rules_json=["crosses_above:ema20:ema50"],
        feature_snapshot_hash="h" * 64,
        data_source="series:1/v1",
    )
    db_session.add(signal)
    db_session.flush()
    return signal.id


def test_explain_persisted_signal_attaches_explanation(db_session) -> None:
    _provider(db_session)
    signal_id = _seed_signal(db_session)
    calls: list = []
    factory = make_factory(CANNED_SIGNAL_EXPLANATION, calls)

    result = explain_signal(db_session, signal_id, router_factory=factory)
    assert result["cached"] is False

    signal = db_session.get(Signal, signal_id)
    assert signal is not None
    assert signal.explanation_json is not None
    assert signal.explanation_json["summary"].startswith("价格上穿")
    assert signal.ai_task_id == result["task_id"]


def test_explain_missing_signal_404(db_session) -> None:
    _provider(db_session)
    with pytest.raises(LookupError):
        explain_signal(db_session, 999999)


def _seed_backtest(db_session) -> int:
    strategy = Strategy(name="AI BT", slug="ai-bt")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={},
        immutable_hash="y" * 64,
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="AIBT")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="ai-src", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        dataset_hash="z" * 64,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    result = BacktestResult(
        backtest_run_id=run.id,
        summary_json={"total_return": 0.12, "max_drawdown": -0.05, "sharpe": 1.1},
        metrics_json={},
        result_hash="r" * 64,
    )
    db_session.add(result)
    db_session.flush()
    return run.id


def test_explain_backtest_uses_stored_stats(db_session) -> None:
    _provider(db_session)
    run_id = _seed_backtest(db_session)
    calls: list = []
    factory = make_factory(CANNED_BACKTEST_EXPLANATION, calls)

    result = explain_backtest(db_session, run_id, router_factory=factory)
    assert result["cached"] is False
    assert "微利" in result["explanation"]["summary"]
    # Facts passed to the model are the stored numbers, unchanged.
    facts = calls[0].structured_facts
    assert facts["summary"]["total_return"] == 0.12
    assert facts["summary"]["sharpe"] == 1.1


def test_explain_backtest_requires_completed_run(db_session) -> None:
    _provider(db_session)
    run_id = _seed_backtest(db_session)
    run = db_session.get(BacktestRun, run_id)
    assert run is not None
    run.status = "running"
    db_session.flush()
    with pytest.raises(ValueError, match="not completed"):
        explain_backtest(db_session, run_id)


def test_build_signal_facts_contains_no_computed_stats(db_session) -> None:

    signal_id = _seed_signal(db_session)
    signal = db_session.get(Signal, signal_id)
    assert signal is not None
    facts = build_signal_facts(db_session, signal)
    # The model must only restate these; it must never see return-style stats here.
    assert facts["state"] == "BUY"
    assert facts["price_reference"] == 101.5
    assert "win_rate" not in facts and "sharpe" not in facts


def test_build_backtest_facts_comes_from_storage(db_session) -> None:

    run_id = _seed_backtest(db_session)
    run = db_session.get(BacktestRun, run_id)
    assert run is not None
    facts = build_backtest_facts(db_session, run)
    assert facts["summary"]["total_return"] == 0.12
    assert facts["trade_count"] == 0


def test_api_explain_endpoints(client, db_session) -> None:
    # Unconfigured provider -> 503 with a helpful message, not a 500.
    signal_id = _seed_signal(db_session)
    response = client.post(f"/api/v1/signals/{signal_id}/explain")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]

    status = client.get("/api/v1/ai/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False
