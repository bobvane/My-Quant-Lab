"""Tests for the Phase C explanation (docs/30 §8, ADR-189).

A fake router stands in for the LLM: no test here touches the network. What is
pinned down is the structural claim the feature rests on -- the numbers come from
the analysis, the model may only restate them, and a model that invents a figure
or promises a future produces no explanation at all.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.ai.explain import (
    AI_UNCONFIGURED,
    PERFORMANCE_EXPLANATION_SCHEMA,
    ExplanationRejected,
    build_performance_facts,
    explain_performance,
    explainer_prompt,
)
from app.ai.explanation_guard import (
    check_explanation,
    collect_numbers,
    find_prediction_phrases,
    find_unsupported_numbers,
)
from app.ai.provider import BudgetExceeded, task_capability
from app.domain.models import (
    AIProvider,
    AITask,
    Asset,
    BacktestResult,
    BacktestRun,
    MarketDataSeries,
    MarketDataSource,
    Strategy,
    StrategyVersion,
)
from app.infrastructure.secrets import encrypt_secret

CANNED_PERFORMANCE_EXPLANATION = {
    "conclusion": "这段时间小幅盈利，但没有跑赢买入持有对照。",
    "drivers": ["收益来自一次趋势跟随"],
    "risks": ["回撤期间连续亏损", "对照未计费用，两者并不完全可比"],
    "confidence": "样本交易太少，结论只能当作参考。",
    "next_step": "先在更长的历史区间上重跑一次。",
}

_EQUITY_CURVE = [
    {
        "timestamp": "2024-01-01T00:00:00+00:00",
        "equity": 10000.0,
        "cash": 10000.0,
        "position_value": 0.0,
        "close": 100.0,
    },
    {
        "timestamp": "2024-01-02T00:00:00+00:00",
        "equity": 10200.0,
        "cash": 0.0,
        "position_value": 10200.0,
        "close": 102.0,
    },
    {
        "timestamp": "2024-01-03T00:00:00+00:00",
        "equity": 10100.0,
        "cash": 0.0,
        "position_value": 10100.0,
        "close": 101.0,
    },
    {
        "timestamp": "2024-01-04T00:00:00+00:00",
        "equity": 10600.0,
        "cash": 0.0,
        "position_value": 10600.0,
        "close": 106.0,
    },
]

_METRICS = {
    "initial_capital": 10000.0,
    "final_equity": 10600.0,
    "total_return": 0.06,
    "cagr": 0.07,
    "annualized_volatility": 0.19,
    "sharpe": 0.42,
    "sortino": 0.5,
    "max_drawdown": -0.0098,
    "max_drawdown_duration_bars": 1,
    "number_of_trades": 1,
    "win_rate": 1.0,
    "profit_factor": None,
    "exposure": 0.75,
    "turnover": 1.0,
}


class FakeRouter:
    """Test double with the same shape as ``AIRouter.explain_signal``."""

    def __init__(self, output: dict[str, Any], calls: list) -> None:
        self._output = output
        self.calls = calls

    def explain_signal(self, request, *, spent_today_usd: float = 0.0) -> dict[str, Any]:
        self.calls.append(request)
        if spent_today_usd >= 10**9:
            raise BudgetExceeded("test budget")
        return dict(self._output)


def make_factory(output: dict[str, Any], calls: list):
    def factory(providers: dict, budget: float) -> FakeRouter:
        return FakeRouter(output, calls)

    return factory


def _provider(db_session, *, budget: float = 2.0) -> AIProvider:
    provider = AIProvider(
        name="test-openai-pc",
        provider_type="openai_compatible",
        base_url="http://localhost:9",
        api_key_encrypted=encrypt_secret("sk-test"),
        default_model="test-model",
        daily_budget_usd=budget,
    )
    db_session.add(provider)
    db_session.flush()
    return provider


def _seed_run(db_session, *, status: str = "completed", with_result: bool = True) -> int:
    suffix = uuid4().hex[:8]
    strategy = Strategy(name=f"Phase C {suffix}", slug=f"phase-c-{suffix}")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={},
        immutable_hash="c" * 64,
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol=f"PC{suffix}", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name=f"pc-src-{suffix}", base_url="http://localhost/9")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        dataset_hash="d" * 64,
        status=status,
    )
    db_session.add(run)
    db_session.flush()
    if with_result:
        db_session.add(
            BacktestResult(
                backtest_run_id=run.id,
                summary_json={"total_return": 0.06},
                metrics_json=dict(_METRICS),
                equity_curve_json=[dict(point) for point in _EQUITY_CURVE],
                warnings_json=[],
                result_hash="e" * 64,
            )
        )
        db_session.flush()
    return run.id


def _latest_task(db_session) -> AITask | None:
    return db_session.scalar(select(AITask).order_by(AITask.id.desc()))


# --------------------------------------------------------------------------- #
# The guard: numbers have to exist, promises are refused
# --------------------------------------------------------------------------- #
def test_a_restated_number_is_allowed() -> None:
    facts = {"total_return": 0.182, "trades": 18, "final_equity": 10000.0}
    explanation = {
        "conclusion": "收益 18.2%，一共 18 笔交易，最终资产 10,000。",
        "risks": [],
    }
    assert check_explanation(explanation, facts) == []


def test_rounding_to_display_precision_is_allowed() -> None:
    facts = {"sharpe": 0.1234567}
    assert find_unsupported_numbers({"conclusion": "夏普 0.12。"}, facts) == []


def test_an_invented_number_is_rejected() -> None:
    facts = {"max_drawdown": -0.087}
    violations = check_explanation({"conclusion": "最大回撤 31.4%。"}, facts)
    assert violations == [{"code": "unsupported_number", "detail": "31.4%"}]


def test_a_ratio_may_be_written_as_a_percentage() -> None:
    # 0.06 -> "6%" and 0.06 -> "0.06" are both the same fact.
    assert find_unsupported_numbers({"conclusion": "总收益 6%。"}, {"total_return": 0.06}) == []
    assert find_unsupported_numbers({"conclusion": "总收益 0.06。"}, {"total_return": 0.06}) == []


def test_percentage_may_be_written_as_a_ratio() -> None:
    facts = {"annualized_volatility": 19.0}
    assert find_unsupported_numbers({"conclusion": "波动率 0.19。"}, facts) == []


def test_predictive_wording_is_rejected() -> None:
    explanation = {"conclusion": "预计之后会继续上涨。", "next_step": "we forecast a rebound"}
    phrases = find_prediction_phrases(explanation)
    assert "预计" in phrases
    assert "forecast" in phrases
    codes = {item["code"] for item in check_explanation(explanation, {})}
    assert codes == {"prediction_wording"}


def test_ordinary_recommendation_wording_is_not_flagged() -> None:
    explanation = {"next_step": "下一步应该看更长区间的表现，并观察回撤是否恢复。"}
    assert find_prediction_phrases(explanation) == []


def test_collect_numbers_walks_the_whole_payload() -> None:
    payload = {
        "a": 1,
        "nested": {"b": [2.5, "3"]},
        "label": "区间 2024-01-01",
    }
    found = collect_numbers(payload)
    assert {1.0, 2.5, 3.0, 2024.0} <= found


def test_collect_numbers_ignores_booleans() -> None:
    assert collect_numbers({"flag": True, "off": False}) == set()


# --------------------------------------------------------------------------- #
# The facts: assembled from the analysis, never from a second calculation
# --------------------------------------------------------------------------- #
def test_facts_carry_the_analysis_blocks_verbatim(db_session) -> None:
    run_id = _seed_run(db_session)
    run = db_session.get(BacktestRun, run_id)
    assert run is not None

    from app.data.backtest_service import analysis_for_run

    analysis = analysis_for_run(db_session, run)
    facts = build_performance_facts(db_session, run, analysis)

    assert facts["strategy"]["name"].startswith("Phase C")
    assert facts["strategy"]["version"] == "1.0.0"
    assert facts["strategy"]["symbol"].startswith("PC")
    assert facts["strategy"]["asset_class"] == "stock"
    assert facts["strategy"]["timeframe"] == "1d"
    assert facts["strategy"]["bars"] == analysis["window"]["bars"]
    assert facts["performance"] == analysis["performance"]
    assert facts["risk"] == analysis["risk"]
    assert facts["sample"] == analysis["sample"]
    assert facts["caveats"] == analysis["caveats"]
    # Every figure of the comparison is handed over; its curve is not. A chart is
    # not a fact (§8.2), and hundreds of equity points would only widen what the
    # number check accepts as "supported".
    assert facts["benchmark"] == {
        key: value for key, value in analysis["benchmark"].items() if key != "curve"
    }
    assert analysis["benchmark"]["curve"], "the analysis itself still carries the curve"
    assert "curve" not in facts["benchmark"]
    # The hash is audit material, not a figure the model may quote.
    assert "result_hash" not in facts


def test_the_prompt_is_registered_for_the_new_task() -> None:
    system_prompt, name, version, prompt_hash = explainer_prompt("performance_explanation")
    assert name == "performance_explain"
    assert version == "1.2.0"
    assert prompt_hash
    assert "performance" in system_prompt.lower()
    assert task_capability("performance_explanation") == "standard"
    assert PERFORMANCE_EXPLANATION_SCHEMA["required"] == [
        "conclusion",
        "drivers",
        "risks",
        "confidence",
        "next_step",
    ]


# --------------------------------------------------------------------------- #
# explain_performance
# --------------------------------------------------------------------------- #
def test_explain_performance_returns_an_explanation_and_audits_it(db_session) -> None:
    _provider(db_session)
    run_id = _seed_run(db_session)
    calls: list = []

    result = explain_performance(
        db_session, run_id, router_factory=make_factory(CANNED_PERFORMANCE_EXPLANATION, calls)
    )

    assert result["cached"] is False
    assert result["explanation"]["conclusion"] == CANNED_PERFORMANCE_EXPLANATION["conclusion"]
    assert len(calls) == 1
    assert calls[0].task_type == "performance_explanation"
    assert calls[0].prompt_name == "performance_explain"
    assert calls[0].model is None  # the router chooses; the task never pins one
    # The model sees the analysis, not the raw curve.
    assert "equity_curve" not in calls[0].structured_facts
    task = _latest_task(db_session)
    assert task is not None and task.status == "completed"


def test_explain_performance_rejects_an_invented_number(db_session) -> None:
    _provider(db_session)
    run_id = _seed_run(db_session)
    calls: list = []
    tampered = dict(CANNED_PERFORMANCE_EXPLANATION)
    tampered["conclusion"] = "这段时间收益 88.8%，明显跑赢对照。"

    with pytest.raises(ExplanationRejected) as caught:
        explain_performance(db_session, run_id, router_factory=make_factory(tampered, calls))

    assert [item["code"] for item in caught.value.violations] == ["unsupported_number"]
    assert "88.8%" in str(caught.value)
    # The audit row keeps what the model actually said; only the user-facing
    # explanation is dropped.
    task = _latest_task(db_session)
    assert task is not None and task.status == "completed"


def test_explain_performance_rejects_a_prediction(db_session) -> None:
    _provider(db_session)
    run_id = _seed_run(db_session)
    calls: list = []
    tampered = dict(CANNED_PERFORMANCE_EXPLANATION)
    tampered["next_step"] = "预计下个季度还会继续赚钱。"

    with pytest.raises(ExplanationRejected) as caught:
        explain_performance(db_session, run_id, router_factory=make_factory(tampered, calls))

    assert [item["code"] for item in caught.value.violations] == ["prediction_wording"]


def test_explain_performance_uses_the_cache(db_session) -> None:
    _provider(db_session)
    run_id = _seed_run(db_session)
    calls: list = []
    factory = make_factory(CANNED_PERFORMANCE_EXPLANATION, calls)

    first = explain_performance(db_session, run_id, router_factory=factory)
    second = explain_performance(db_session, run_id, router_factory=factory)

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["task_id"] == first["task_id"]
    assert len(calls) == 1
    assert second["cost_usd_estimated"] == 0.0


def test_explain_performance_requires_a_completed_run(db_session) -> None:
    _provider(db_session)
    running = _seed_run(db_session, status="running")
    with pytest.raises(ValueError, match="not completed"):
        explain_performance(db_session, running)

    completed_without_result = _seed_run(db_session, with_result=False)
    with pytest.raises(ValueError, match="not completed"):
        explain_performance(db_session, completed_without_result)


def test_explain_performance_missing_run(db_session) -> None:
    _provider(db_session)
    with pytest.raises(LookupError):
        explain_performance(db_session, 999999)


def test_explain_performance_unconfigured(db_session) -> None:
    run_id = _seed_run(db_session)
    with pytest.raises(RuntimeError, match=AI_UNCONFIGURED):
        explain_performance(db_session, run_id)


# --------------------------------------------------------------------------- #
# The endpoint: the numbers never depend on any of this
# --------------------------------------------------------------------------- #
def test_endpoint_missing_run_is_404(client, db_session) -> None:
    _provider(db_session)
    assert client.post("/api/v1/backtests/999999/explain-performance").status_code == 404


def test_endpoint_running_run_is_422(client, db_session) -> None:
    _provider(db_session)
    run_id = _seed_run(db_session, status="running")
    response = client.post(f"/api/v1/backtests/{run_id}/explain-performance")
    assert response.status_code == 422


def test_endpoint_without_provider_is_503(client, db_session) -> None:
    run_id = _seed_run(db_session)
    response = client.post(f"/api/v1/backtests/{run_id}/explain-performance")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_endpoint_reports_a_rejected_explanation_as_502(client, db_session, monkeypatch) -> None:
    from app.api.routers import ai as ai_router

    def rejected(db, run_id, **kwargs):
        raise ExplanationRejected([{"code": "unsupported_number", "detail": "88.8%"}])

    monkeypatch.setattr(ai_router, "explain_performance", rejected)
    response = client.post("/api/v1/backtests/1/explain-performance")
    assert response.status_code == 502
    assert "explanation rejected" in response.json()["detail"]


def test_endpoint_returns_the_explanation_envelope(client, db_session, monkeypatch) -> None:
    from app.api.routers import ai as ai_router

    def explained(db, run_id, **kwargs):
        return {
            "explanation": dict(CANNED_PERFORMANCE_EXPLANATION),
            "cached": True,
            "task_id": 7,
            "model": "test-model",
            "cost_usd_estimated": 0.0,
        }

    monkeypatch.setattr(ai_router, "explain_performance", explained)
    response = client.post("/api/v1/backtests/1/explain-performance")
    assert response.status_code == 200
    body = response.json()
    assert body["cached"] is True
    assert body["task_id"] == 7
    assert body["explanation"]["conclusion"] == CANNED_PERFORMANCE_EXPLANATION["conclusion"]
