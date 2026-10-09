"""Strategy version management + paper order/trade queries (docs/12)."""

from __future__ import annotations

import datetime as dt

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "ver", "name": "Ver", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def test_activate_version_switches_current(client) -> None:
    strategy = client.post("/api/v1/strategies", json={"name": "Versioned"}).json()
    v1 = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": _DSL},
    ).json()
    v2 = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.1.0", "dsl": _DSL},
    ).json()

    listed = client.get(f"/api/v1/strategy-versions?strategy_id={strategy['id']}").json()
    assert {row["id"] for row in listed} == {v1["id"], v2["id"]}
    assert next(row for row in listed if row["id"] == v2["id"])["is_current"] is True

    activated = client.put(f"/api/v1/strategy-versions/{v1['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_current"] is True

    refreshed = client.get(f"/api/v1/strategy-versions/{v2['id']}").json()
    assert refreshed["is_current"] is False

    audit = client.get("/api/v1/audit/logs").json()
    assert any(e["event_type"] == "strategy_version_activated" for e in audit["events"])


def test_version_lookup_and_parameters(client) -> None:
    strategy = client.post("/api/v1/strategies", json={"name": "Params"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": _DSL, "parameters": {"fast": 10}},
    ).json()
    assert client.get(f"/api/v1/strategy-versions/{version['id']}").status_code == 200
    assert client.get("/api/v1/strategy-versions/9999").status_code == 404

    params = client.get(f"/api/v1/strategy-versions/{version['id']}/parameters").json()
    assert params and params[0]["parameters"] == {"fast": 10}


def test_signal_list_is_enriched(client, db_session) -> None:
    from app.domain.models import Asset, Signal, Strategy, StrategyVersion

    strategy = Strategy(name="Enrich", slug="enrich-strat")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="2.0.0", dsl_json={}, immutable_hash="e" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="ENR", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        Signal(
            strategy_version_id=version.id,
            asset_id=asset.id,
            timeframe="1d",
            bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
            state="BUY",
            direction="LONG",
            price_reference=12.5,
            triggered_rules_json=["gt:close:ema20"],
            feature_snapshot_hash="e" * 64,
            data_source="test",
        )
    )
    db_session.commit()

    rows = client.get("/api/v1/signals").json()
    assert rows
    row = rows[0]
    assert row["symbol"] == "ENR"
    assert row["strategy_name"] == "Enrich"
    assert row["strategy_version"] == "2.0.0"


def test_paper_orders_and_trades_are_queryable(client, db_session) -> None:
    from app.domain.models import Asset, Signal, Strategy, StrategyVersion

    account = client.post(
        "/api/v1/paper/accounts", json={"name": "Query PA", "initial_cash": 5000}
    ).json()
    strategy = Strategy(name="Query", slug="query-strat")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="v" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="QPA", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=50.0,
        feature_snapshot_hash="v" * 64,
        data_source="test",
    )
    db_session.add(signal)
    db_session.commit()

    executed = client.post(
        f"/api/v1/paper/accounts/{account['id']}/execute", json={"signal_id": signal.id}
    )
    assert executed.status_code == 200, executed.text
    order_id = executed.json()["order_id"]

    orders = client.get(f"/api/v1/paper/orders?account_id={account['id']}").json()
    assert len(orders) == 1 and orders[0]["side"] == "BUY"
    account_orders = client.get(f"/api/v1/paper/accounts/{account['id']}/orders").json()
    assert len(account_orders) == 1
    assert client.get("/api/v1/paper/accounts/9999/orders").status_code == 404
    assert client.get(f"/api/v1/paper/orders/{order_id}").json()["status"] == "filled"
    assert client.get("/api/v1/paper/orders/9999").status_code == 404

    trades = client.get(f"/api/v1/paper/trades?account_id={account['id']}").json()
    assert len(trades) == 1 and trades[0]["account_id"] == account["id"]
    # ADR-204: the row carries the order and the signal it came from, plus the costs the
    # engine already stored, so a fill can be traced back instead of showing 「未知」.
    assert trades[0]["order_id"] == order_id
    assert trades[0]["signal_id"] == signal.id
    assert trades[0]["fees"] is not None and trades[0]["slippage"] is not None

    account_trades = client.get(f"/api/v1/paper/accounts/{account['id']}/trades").json()
    assert len(account_trades) == 1
    assert "account_id" not in account_trades[0]
    assert account_trades[0]["order_id"] == order_id
    assert account_trades[0]["signal_id"] == signal.id
