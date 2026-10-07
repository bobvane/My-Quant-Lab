"""Phase C analysis tests (docs/30, ADR-187/188).

Two layers are pinned here:

* ``app.research.analysis`` is a pure function over artifacts the engine already
  stored, so the boundary rules (NaN, one bar, no trades, no recovery, missing
  closes) are tested without a database at all;
* ``GET /backtests/{run_id}/analysis`` is read-only, deterministic and never
  turns an incomplete run into a 500.
"""

from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from app.domain.models import BacktestRun
from app.research.analysis import (
    ANALYSIS_VERSION,
    MIN_TRADES_ENOUGH,
    MIN_TRADES_PRELIMINARY,
    analyse_run,
    curve_has_closes,
    curve_window,
)
from app.research.monte_carlo import MIN_TRADES_FOR_CONFIDENCE
from app.strategies.lifecycle import LifecycleThresholds

_SYMBOL = "DEMO-AAPL"

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "phase-c", "name": "Phase C", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fee_bps": 10, "slippage_bps": 5, "initial_capital": 10_000.0},
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _curve(
    values: list[float],
    *,
    closes: list[float] | None = None,
    start: str = "2024-01-01",
    step: dt.timedelta = dt.timedelta(days=1),
) -> list[dict]:
    moment = dt.datetime.fromisoformat(start).replace(tzinfo=dt.UTC)
    prices = closes if closes is not None else [100.0 + index for index in range(len(values))]
    return [
        {
            "timestamp": (moment + step * index).isoformat(),
            "equity": value,
            "cash": value,
            "position_value": 0.0,
            "close": prices[index],
        }
        for index, value in enumerate(values)
    ]


def _analysis(**overrides) -> dict:
    payload = {
        "run_id": 1,
        "result_hash": "hash",
        "metrics": {"initial_capital": 10_000.0, "final_equity": 11_000.0, "total_return": 0.1},
        "equity_curve": _curve([10_000.0, 10_500.0, 11_000.0]),
        "trades": [],
    }
    payload.update(overrides)
    return analyse_run(**payload)


def _completed_run(client) -> int:
    client.post("/api/v1/market-data/sync", json={"symbol": _SYMBOL, "timeframe": "1d"})
    strategy = client.post("/api/v1/strategies", json={"name": "Phase C"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(_DSL)},
    ).json()
    run = client.post(
        "/api/v1/backtests",
        json={"strategy_version_id": version["id"], "symbol": _SYMBOL, "timeframe": "1d"},
    ).json()
    return int(run["id"])


# --------------------------------------------------------------------------- #
# pure analysis
# --------------------------------------------------------------------------- #
def test_stored_metrics_are_returned_verbatim() -> None:
    metrics = {
        "initial_capital": 10_000.0,
        "final_equity": 11_000.0,
        "total_return": 0.1,
        "number_of_trades": 12,
        "notes": ["kept as stored"],
    }
    analysis = _analysis(metrics=metrics)

    # The engine's block is passed through, not rebuilt: an int stays an int and an
    # extra key survives, so a reader can diff it against the stored row.
    assert analysis["performance"]["stored"] == metrics
    assert analysis["performance"]["stored"] is not metrics
    assert analysis["analysis_version"] == ANALYSIS_VERSION


def test_derived_numbers_follow_their_definitions() -> None:
    analysis = _analysis(
        metrics={
            "initial_capital": 10_000.0,
            "final_equity": 11_000.0,
            "total_return": 0.1,
            "cagr": 0.4,
            "max_drawdown": -0.05,
            "max_drawdown_duration_bars": 3,
        },
        equity_curve=_curve([10_000.0, 10_500.0, 9_975.0, 10_800.0, 11_000.0]),
        trades=[
            {"pnl": 120.0, "exit_time": "2024-01-02T00:00:00+00:00", "direction": "LONG"},
            {"pnl": -40.0, "exit_time": "2024-01-03T00:00:00+00:00", "direction": "LONG"},
        ],
    )

    derived = analysis["performance"]["derived"]
    assert derived["calmar"] == pytest.approx(0.4 / 0.05)
    assert derived["worst_bar_return"] == pytest.approx(-0.05)
    # closes rise 1.0 per bar from 100: the comparison earns 4% over the same window.
    assert analysis["benchmark"]["total_return"] == pytest.approx(0.04)
    assert derived["excess_return"] == pytest.approx(0.1 - 0.04)

    risk = analysis["risk"]
    assert risk["max_drawdown"] == pytest.approx(-0.05)
    assert risk["max_drawdown_duration_bars"] == 3
    assert risk["max_drawdown_duration_days"] == pytest.approx(3.0)
    assert risk["recovery_bars"] == 1
    assert risk["recovered"] is True
    assert risk["worst_trade"]["pnl"] == pytest.approx(-40.0)
    assert risk["worst_trade"]["direction"] == "LONG"


def test_worst_month_uses_calendar_months() -> None:
    start = "2024-01-30"
    curve = _curve([10_000.0, 10_200.0, 9_500.0, 9_600.0], start=start)
    analysis = _analysis(metrics={"initial_capital": 10_000.0}, equity_curve=curve)

    # Jan: 10_000 -> 10_200 (+2%); Feb: 9_500 -> 9_600 (+1.05%). The worst month is
    # the *change inside a month*, never the drawdown across the boundary.
    expected = 9_600.0 / 9_500.0 - 1.0
    assert analysis["risk"]["worst_month_return"] == pytest.approx(expected)


def test_a_month_with_one_bar_is_not_reported_as_the_worst_month() -> None:
    analysis = _analysis(metrics={"initial_capital": 10_000.0}, equity_curve=_curve([10_000.0]))
    assert analysis["risk"]["worst_month_return"] is None


def test_zero_downside_deviation_is_zero_not_null() -> None:
    analysis = _analysis(equity_curve=_curve([10_000.0, 10_100.0, 10_200.0]))
    assert analysis["risk"]["downside_deviation"] == 0.0


def test_flat_curve_has_no_calmar_and_nothing_to_recover() -> None:
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0, "final_equity": 10_000.0, "max_drawdown": 0.0},
        equity_curve=_curve([10_000.0, 10_000.0, 10_000.0]),
    )
    assert analysis["performance"]["derived"]["calmar"] is None
    assert analysis["risk"]["max_drawdown"] == 0.0
    assert analysis["risk"]["recovery_bars"] == 0
    assert analysis["risk"]["recovered"] is True


def test_zero_volatility_keeps_volatility_and_withholds_sharpe() -> None:
    # The comparison goes through the engine's compute_metrics, so a perfectly flat
    # price series reports volatility 0.0 and no Sharpe rather than a faked ratio.
    flat_prices = _curve([10_000.0, 10_000.0, 10_000.0], closes=[100.0, 100.0, 100.0])
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0, "final_equity": 10_000.0},
        equity_curve=flat_prices,
    )
    assert analysis["benchmark"]["annualized_volatility"] == 0.0
    assert analysis["benchmark"]["sharpe"] is None


def test_total_loss_drawdown_is_reported_as_unrecovered() -> None:
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0, "final_equity": 0.0, "max_drawdown": -1.0},
        equity_curve=_curve([10_000.0, 5_000.0, 0.0]),
    )
    assert analysis["risk"]["max_drawdown"] == pytest.approx(-1.0)
    assert analysis["risk"]["recovery_bars"] is None
    assert analysis["risk"]["recovered"] is False
    assert "drawdown_not_recovered" in {item["code"] for item in analysis["caveats"]}


def test_no_closed_trades_leaves_trade_metrics_unknown_but_keeps_the_comparison() -> None:
    analysis = _analysis(trades=[])
    assert analysis["risk"]["worst_trade"] is None
    assert analysis["risk"]["max_consecutive_losses"] is None
    assert analysis["sample"]["tier"] == "insufficient"
    assert analysis["benchmark"] is not None
    assert "no_closed_trades" in {item["code"] for item in analysis["caveats"]}


def test_empty_curve_reports_nulls_and_caveats() -> None:
    analysis = _analysis(metrics={}, equity_curve=[], trades=[])
    body = json.dumps(analysis)
    assert "NaN" not in body and "Infinity" not in body

    assert analysis["window"] == {"start": None, "end": None, "bars": 0}
    assert analysis["benchmark"] is None
    assert all(value is None for value in analysis["risk"].values() if not isinstance(value, str))
    codes = {item["code"] for item in analysis["caveats"]}
    assert {"no_equity_curve", "benchmark_unavailable"} <= codes


def test_single_bar_curve_has_no_ratios() -> None:
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0, "final_equity": 10_000.0},
        equity_curve=_curve([10_000.0]),
    )
    assert analysis["window"]["bars"] == 1
    assert analysis["risk"]["worst_bar_return"] is None
    assert analysis["risk"]["downside_deviation"] is None
    assert analysis["performance"]["derived"]["calmar"] is None
    assert "curve_too_short" in {item["code"] for item in analysis["caveats"]}


def test_non_positive_initial_capital_withholds_the_ratios() -> None:
    analysis = _analysis(
        metrics={"initial_capital": 0.0, "final_equity": 500.0},
        equity_curve=_curve([0.0, 200.0, 500.0]),
    )
    assert analysis["benchmark"] is None
    assert analysis["performance"]["derived"]["calmar"] is None
    codes = {item["code"] for item in analysis["caveats"]}
    assert "non_positive_initial_capital" in codes


def test_nan_and_infinity_never_reach_the_payload() -> None:
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0, "final_equity": 11_000.0, "total_return": 0.1},
        equity_curve=_curve([10_000.0, float("nan"), float("inf"), 11_000.0]),
    )
    body = json.dumps(analysis, allow_nan=False)
    assert "non_finite_value" in {item["code"] for item in analysis["caveats"]}
    assert analysis["window"]["bars"] == 2
    assert analysis["risk"]["worst_bar_return"] == pytest.approx(0.1)
    assert "nan" not in body.lower()


def test_sample_tier_mirrors_the_existing_evidence_gates() -> None:
    assert LifecycleThresholds().min_backtest_trades == MIN_TRADES_PRELIMINARY
    assert MIN_TRADES_ENOUGH == MIN_TRADES_FOR_CONFIDENCE

    def tier(trades: int) -> str:
        return _analysis(trades=[{"pnl": 1.0}] * trades)["sample"]["tier"]

    assert tier(1) == "insufficient"
    assert tier(MIN_TRADES_PRELIMINARY - 1) == "insufficient"
    assert tier(MIN_TRADES_PRELIMINARY) == "preliminary"
    assert tier(MIN_TRADES_ENOUGH - 1) == "preliminary"
    assert tier(MIN_TRADES_ENOUGH) == "enough"


def test_benchmark_uses_the_stored_closes_over_the_same_window() -> None:
    curve = _curve([10_000.0, 9_000.0, 11_000.0], closes=[200.0, 190.0, 220.0])
    analysis = _analysis(metrics={"initial_capital": 10_000.0}, equity_curve=curve)

    benchmark = analysis["benchmark"]
    assert benchmark["source"] == "equity_curve_close"
    assert benchmark["window_matched"] is True
    assert benchmark["bars_matched"] == analysis["window"]["bars"] == 3
    assert benchmark["fees_included"] is False
    assert benchmark["label"] == "买入持有对照"
    # 200 -> 220 on the same bars.
    assert benchmark["total_return"] == pytest.approx(0.1)
    assert benchmark["final_equity"] == pytest.approx(11_000.0)
    assert benchmark["max_drawdown"] == pytest.approx(190.0 / 200.0 - 1.0)
    # The curve is the same money as the strategy's own and carries its own
    # timestamps, so the browser never rebuilds or re-aligns it.
    assert benchmark["curve"] == [
        {"timestamp": "2024-01-01T00:00:00+00:00", "equity": 10_000.0},
        {"timestamp": "2024-01-02T00:00:00+00:00", "equity": 9_500.0},
        {"timestamp": "2024-01-03T00:00:00+00:00", "equity": 11_000.0},
    ]


def test_benchmark_falls_back_to_series_bars_when_closes_are_missing() -> None:
    stripped = [
        {k: v for k, v in point.items() if k != "close"} for point in _curve([10_000.0, 11_000.0])
    ]
    assert curve_has_closes(stripped) is False
    assert curve_window(stripped)[0] is not None

    analysis = _analysis(
        metrics={"initial_capital": 10_000.0},
        equity_curve=stripped,
        fallback_bars=[
            {"timestamp": "2024-01-01T00:00:00+00:00", "close": 50.0},
            {"timestamp": "2024-01-02T00:00:00+00:00", "close": 55.0},
        ],
    )
    benchmark = analysis["benchmark"]
    assert benchmark["source"] == "series_bars"
    assert benchmark["window_matched"] is True
    assert benchmark["bars_matched"] == 2
    assert benchmark["total_return"] == pytest.approx(0.1)
    # The fallback path keeps only the bars whose timestamp actually matched, so a
    # partial window cannot be drawn as if it covered the whole run.
    assert [point["timestamp"] for point in benchmark["curve"]] == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-02T00:00:00+00:00",
    ]
    assert [point["equity"] for point in benchmark["curve"]] == [10_000.0, 11_000.0]


def test_a_partial_benchmark_window_is_flagged_not_hidden() -> None:
    stripped = [
        {k: v for k, v in point.items() if k != "close"} for point in _curve([10_000.0, 11_000.0])
    ]
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0},
        equity_curve=stripped,
        fallback_bars=[{"timestamp": "2024-01-01T00:00:00+00:00", "close": 50.0}],
    )
    assert analysis["benchmark"]["window_matched"] is False
    assert analysis["benchmark"]["bars_matched"] == 1
    # One matched bar means one drawable point: the flag and the series agree.
    assert len(analysis["benchmark"]["curve"]) == 1
    assert "benchmark_partial_window" in {item["code"] for item in analysis["caveats"]}


def test_a_missing_comparison_is_never_fabricated() -> None:
    stripped = [
        {k: v for k, v in point.items() if k != "close"} for point in _curve([10_000.0, 11_000.0])
    ]
    analysis = _analysis(
        metrics={"initial_capital": 10_000.0}, equity_curve=stripped, fallback_bars=[]
    )
    assert analysis["benchmark"] is None
    assert analysis["performance"]["derived"]["excess_return"] is None
    assert "benchmark_unavailable" in {item["code"] for item in analysis["caveats"]}


def test_crypto_gets_the_calendar_caveat() -> None:
    analysis = _analysis(asset_class="crypto")
    assert "bars_per_year_252_for_crypto" in {item["code"] for item in analysis["caveats"]}
    assert "bars_per_year_252_for_crypto" not in {
        item["code"] for item in _analysis(asset_class="stock")["caveats"]
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_analysis_endpoint_answers_the_contract(client) -> None:
    run_id = _completed_run(client)

    response = client.get(f"/api/v1/backtests/{run_id}/analysis")
    assert response.status_code == 200, response.text
    body = response.json()
    stored = client.get(f"/api/v1/backtests/{run_id}").json()

    assert body["run_id"] == run_id
    assert body["result_hash"] == stored["result_hash"]
    assert body["analysis_version"] == ANALYSIS_VERSION
    # The engine's block is byte-for-byte the stored one, including value types.
    assert body["performance"]["stored"] == stored["metrics"]
    assert body["window"]["bars"] == len(stored["equity_curve"])
    assert body["benchmark"]["source"] == "equity_curve_close"
    assert body["benchmark"]["window_matched"] is True
    assert body["benchmark"]["bars_matched"] == body["window"]["bars"]
    assert body["sample"]["tier"] in {"insufficient", "preliminary", "enough"}
    assert body["caveats"] == list(body["caveats"])


def test_analysis_endpoint_is_read_only_and_deterministic(client) -> None:
    run_id = _completed_run(client)
    before = client.get(f"/api/v1/backtests/{run_id}").json()

    first = client.get(f"/api/v1/backtests/{run_id}/analysis")
    second = client.get(f"/api/v1/backtests/{run_id}/analysis")

    assert first.content == second.content
    after = client.get(f"/api/v1/backtests/{run_id}").json()
    assert after == before


def test_analysis_never_returns_non_finite_numbers(client) -> None:
    run_id = _completed_run(client)
    text = client.get(f"/api/v1/backtests/{run_id}/analysis").text
    assert "NaN" not in text and "Infinity" not in text


def test_analysis_of_an_unknown_run_is_404(client) -> None:
    response = client.get("/api/v1/backtests/999999/analysis")
    assert response.status_code == 404, response.text


def test_analysis_of_a_run_without_a_result_is_409(client, db_session) -> None:
    run_id = _completed_run(client)
    source = db_session.get(BacktestRun, run_id)
    pending = BacktestRun(
        strategy_version_id=source.strategy_version_id,
        dataset_version_id=source.dataset_version_id,
        engine_version=source.engine_version,
        feature_version=source.feature_version,
        parameters_json={},
        execution_model_json={},
        dataset_hash=source.dataset_hash,
        status="running",
    )
    db_session.add(pending)
    db_session.commit()

    response = client.get(f"/api/v1/backtests/{pending.id}/analysis")
    assert response.status_code == 409, response.text
    assert "completed" in response.json()["detail"]
