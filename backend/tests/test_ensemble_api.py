"""Ensemble API tests (docs/24, ADR-047)."""

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
    # These two members share an entry bar in the fixture (5/20 AND 10/30).
    return _version(client, "ens-a", 5, 20), _version(client, "ens-b", 10, 30)


def test_ensemble_endpoint_combines_members(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [
                {"strategy_version_id": a, "weight": 1.0},
                {"strategy_version_id": b, "weight": 1.0},
            ],
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "vote_threshold": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["ensemble_version"]
    assert body["vote_threshold"] == 0.5
    assert body["bars_evaluated"] > 0
    assert len(body["members"]) == 2
    # Weights are normalised.
    assert sum(m["weight"] for m in body["members"]) == 1.0
    agreement = body["agreement"]
    assert agreement["entries_taken"] == len(body["trades"])
    assert agreement["entries_taken"] <= min(m["entry_bars"] for m in body["members"])
    assert body["initial_capital"] == 10_000.0
    assert "total_return" in body["metrics"]


def test_ensemble_reports_which_dataset_it_ran_on(client) -> None:
    """Callers must be able to tell whether a member's stored run is comparable.

    Without the dataset identity the comparison table shows a member's numbers from a
    possibly different symbol/timeframe next to the portfolio's with no way to notice.
    """

    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [
                {"strategy_version_id": a, "weight": 1.0},
                {"strategy_version_id": b, "weight": 1.0},
            ],
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "vote_threshold": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset_version_id"] is not None
    assert body["symbol"] == _SYMBOL
    assert body["timeframe"] == "1d"
    # These two were computed by the engine and previously dropped by the response model.
    assert body["engine_version"] == "ensemble-1.1.0"
    assert body["feature_version"] == "ensemble"


def test_ensemble_reports_support_and_solo_signals(client) -> None:
    """Attribution numbers must be internally consistent and present per member."""

    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [
                {"strategy_version_id": a, "weight": 1.0},
                {"strategy_version_id": b, "weight": 1.0},
            ],
            "symbol": _SYMBOL,
            "vote_threshold": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    agreement = response.json()["agreement"]
    members = response.json()["members"]
    assert agreement["signalled_bars"] >= agreement["entry_bars"]
    assert agreement["solo_signalled_bars"] <= agreement["signalled_bars"]
    for member in members:
        assert member["entry_agreed"] <= member["entry_bars"]
        assert member["solo_entries"] <= member["entry_bars"]
        assert member["entry_support_rate"] is None or (0.0 <= member["entry_support_rate"] <= 1.0)
        assert member["vote_agreement_rate"] is None or (
            0.0 <= member["vote_agreement_rate"] <= 1.0
        )
    # A member can never be supported on more proposal bars than the ensemble had.
    assert all(m["entry_agreed"] <= agreement["entry_bars"] for m in members)


def test_ensemble_reports_same_bar_member_runs(client) -> None:
    """Each member is reported on the ensemble's own bars and cost model.

    The old comparison table read each member's most recent stored backtest, which could
    have run on another window or fee model. These runs are produced by the ensemble
    itself, so the table no longer compares incommensurable numbers.
    """

    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [
                {"strategy_version_id": a, "weight": 1.0},
                {"strategy_version_id": b, "weight": 1.0},
            ],
            "symbol": _SYMBOL,
            "timeframe": "1d",
            "vote_threshold": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    runs = body["member_runs"]
    assert len(runs) == len(body["members"]) == 2
    assert [run["label"] for run in runs] == [member["label"] for member in body["members"]]

    bars = body["bars_evaluated"]
    for run in runs:
        # Same bars as the portfolio: this is what makes the comparison legitimate.
        assert len(run["equity_curve"]) == bars
        assert run["initial_capital"] > 0
        assert "total_return" in run["metrics"]
        # `entries_taken` counts positions opened; the metric counts what was reported.
        assert run["metrics"]["number_of_trades"] > 0
    assert sum(run["initial_capital"] for run in runs) == pytest.approx(body["initial_capital"])

    # The portfolio's total return has to be one of the outcomes a single-account run can
    # reach: bounded by the best and worst solo run. A value outside that range would mean
    # the portfolio is trading something no member did (or the two used different cost
    # models), which is exactly the comparison this report exists to make honest.
    returns = [run["metrics"]["total_return"] for run in runs]
    assert min(returns) <= body["metrics"]["total_return"] <= max(returns)


def test_ensemble_single_member_matches_its_own_solo_run(client) -> None:
    """With one member the vote cannot change anything, so the two must coincide."""

    a, _ = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a, "weight": 1.0}],
            "symbol": _SYMBOL,
            "vote_threshold": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    run = body["member_runs"][0]
    assert run["final_equity"] == pytest.approx(body["final_equity"])
    assert run["equity_curve"] == body["equity_curve"]
    assert run["metrics"]["total_return"] == pytest.approx(body["metrics"]["total_return"])


def test_ensemble_report_is_compressed_when_the_client_asks(client) -> None:
    """The report is large because it carries a curve per member, so it must compress.

    Without gzip the response is hundreds of KB of JSON that the browser has to pull
    through the proxy on every run. This asserts the client that advertises gzip still
    gets back the same JSON, and gets less of it over the wire.
    """

    a, b = _seed(client)
    payload = {
        "members": [
            {"strategy_version_id": a, "weight": 1.0},
            {"strategy_version_id": b, "weight": 1.0},
        ],
        "symbol": _SYMBOL,
        "vote_threshold": 0.5,
    }
    compressed = client.post(
        "/api/v1/research/ensemble", json=payload, headers={"Accept-Encoding": "gzip"}
    )
    plain = client.post(
        "/api/v1/research/ensemble", json=payload, headers={"Accept-Encoding": "identity"}
    )
    assert compressed.status_code == plain.status_code == 200, compressed.text
    assert compressed.headers.get("content-encoding") == "gzip"
    assert plain.headers.get("content-encoding") is None

    # httpx transparently decodes, so this is the real payload either way: the two
    # requests must have produced the same report.
    assert compressed.json()["metrics"] == plain.json()["metrics"]
    assert compressed.json()["member_runs"] == plain.json()["member_runs"]

    # httpx transparently decodes, so `content` is the JSON either way. The compressed
    # response has no `Content-Length` header, which is what proves the body really was
    # re-encoded: an untouched body would still advertise its original length.
    assert plain.headers.get("content-length") == str(len(plain.content))
    assert compressed.headers.get("content-length") is None
    assert compressed.headers.get("vary") is not None


def test_ensemble_is_reproducible(client) -> None:
    a, b = _seed(client)
    payload = {
        "members": [{"strategy_version_id": a}, {"strategy_version_id": b}],
        "symbol": _SYMBOL,
        "vote_threshold": 0.5,
    }
    first = client.post("/api/v1/research/ensemble", json=payload)
    second = client.post("/api/v1/research/ensemble", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["metrics"] == second.json()["metrics"]
    assert first.json()["agreement"] == second.json()["agreement"]


def test_ensemble_rejects_duplicate_members(client) -> None:
    """The same version twice must be refused, not silently treated as agreement.

    Two entries of one version normalise to 0.5 + 0.5, so the strict-majority threshold
    is satisfied by that version's own signal alone — a "vote" with a single
    participant. That silently misrepresents the result, so it is a 422.
    """

    a, _ = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a}, {"strategy_version_id": a}],
            "symbol": _SYMBOL,
        },
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "duplicate" in detail
    assert str(a) in detail


def test_ensemble_accepts_a_non_current_version(client) -> None:
    """A superseded version must stay usable as a member.

    Strategy versions are immutable snapshots, so an older DSL remains a legitimate
    voting member after a newer version of the SAME strategy became current, and the
    report labels it by its own version string. This path was previously untested.
    """

    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    strategy = client.post("/api/v1/strategies", json={"name": "supersede"}).json()

    def add_version(version: str, fast: int) -> int:
        response = client.post(
            f"/api/v1/strategies/{strategy['id']}/versions",
            json={"version": version, "dsl": copy.deepcopy(_dsl("super", fast, 20))},
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    older = add_version("1.0.0", 5)
    newer = add_version("2.0.0", 10)

    assert client.get(f"/api/v1/strategy-versions/{older}").json()["is_current"] is False
    assert client.get(f"/api/v1/strategy-versions/{newer}").json()["is_current"] is True

    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": older}, {"strategy_version_id": newer}],
            "symbol": _SYMBOL,
        },
    )
    assert response.status_code == 200, response.text
    labels = [m["label"] for m in response.json()["members"]]
    assert labels == ["1@1.0.0", "1@2.0.0"]


def test_ensemble_unknown_member_is_404(client) -> None:
    _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={"members": [{"strategy_version_id": 999_999}], "symbol": _SYMBOL},
    )
    assert response.status_code == 404, response.text


def test_ensemble_requires_at_least_one_member(client) -> None:
    response = client.post("/api/v1/research/ensemble", json={"members": [], "symbol": _SYMBOL})
    assert response.status_code == 422, response.text


def test_ensemble_rejects_threshold_one(client) -> None:
    """The vote must strictly exceed the threshold, so 1.0 is unreachable."""

    a, _ = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a}],
            "symbol": _SYMBOL,
            "vote_threshold": 1.0,
        },
    )
    assert response.status_code == 422, response.text


def test_ensemble_rejects_negative_weight(client) -> None:
    a, _ = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={"members": [{"strategy_version_id": a, "weight": -1.0}], "symbol": _SYMBOL},
    )
    assert response.status_code == 422, response.text


def test_ensemble_missing_series_is_404(client) -> None:
    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a}, {"strategy_version_id": b}],
            "symbol": "NO-SUCH-SYMBOL",
        },
    )
    assert response.status_code == 404, response.text


def test_ensemble_disjoint_members_produce_no_positions(client) -> None:
    """Two members that never agree must open nothing, not a union."""

    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    fast = _version(client, "dis-fast", 5, 20)
    slow = _version(client, "dis-slow", 20, 60)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": fast}, {"strategy_version_id": slow}],
            "symbol": _SYMBOL,
            "vote_threshold": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agreement"]["entry_bars"] == 0
    assert body["trades"] == []


def test_ensemble_execution_overrides_reach_the_portfolio(client) -> None:
    a, b = _seed(client)
    base = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a}, {"strategy_version_id": b}],
            "symbol": _SYMBOL,
        },
    ).json()
    cheap = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a}, {"strategy_version_id": b}],
            "symbol": _SYMBOL,
            "execution_overrides": {"fee_bps": 1},
        },
    ).json()
    assert cheap["agreement"] == base["agreement"]
    if base["trades"]:
        assert cheap["final_equity"] > base["final_equity"]


def test_ensemble_records_an_audit_event(client, db_session) -> None:
    from sqlalchemy import select

    from app.domain.models import AuditLog

    a, b = _seed(client)
    response = client.post(
        "/api/v1/research/ensemble",
        json={
            "members": [{"strategy_version_id": a}, {"strategy_version_id": b}],
            "symbol": _SYMBOL,
        },
    )
    assert response.status_code == 200, response.text

    events = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type == "ensemble_completed")
    ).all()
    assert len(events) == 1
    payload = events[0].payload_json
    assert len(payload["members"]) == 2
    assert payload["vote_threshold"] == 0.5
