"""Ghostfolio holdings parsing + signal portfolio context (docs/09 §3, docs/10).

Regression target: the activity aggregation referenced ``profile`` even when it
had never been assigned, so any activity that carried a flat ``symbol`` crashed
the endpoint (HTTP 500 on a real portfolio). Parsing must tolerate every field
spelling and never raise.
"""

from __future__ import annotations

from app.data.ghostfolio import GhostfolioAdapter
from app.simulation.signal_engine import portfolio_context_for

# Construction only stores config; these tests never touch the network.
ADAPTER = GhostfolioAdapter(base_url="http://ghostfolio.local", token="x" * 32)


def test_holdings_payload_with_fraction_allocation() -> None:
    payload = {
        "accounts": [{"id": 1}],
        "holdings": {
            "AAPL": {
                "symbol": "AAPL",
                "name": "Apple",
                "quantity": 10,
                "marketPrice": 180.0,
                "valueInBaseCurrency": 1800.0,
                "allocationInPercentage": 0.25,
                "currency": "USD",
            }
        },
    }
    holdings = ADAPTER._parse_holdings_payload(payload)
    assert len(holdings) == 1
    holding = holdings[0]
    assert holding["symbol"] == "AAPL"
    assert holding["value"] == 1800.0
    assert holding["allocation_pct"] == 25.0
    summary = ADAPTER._summarise(holdings, accounts_count=1, source="holdings")
    assert summary["holdings_count"] == 1
    assert summary["total_value"] == 1800.0


def test_holdings_list_items_with_asset_profile() -> None:
    """Real Ghostfolio shape: a list of items whose symbol lives in assetProfile."""

    payload = {
        "holdings": [
            {
                "assetProfile": {"symbol": "QQQ", "name": "Invesco QQQ", "currency": "USD"},
                "quantity": 15,
                "marketPrice": 500.0,
                "valueInBaseCurrency": 7500.0,
                "investment": 6000.0,
                "allocationInPercentage": 0.2,
            }
        ]
    }
    holdings = ADAPTER._parse_holdings_payload(payload)
    assert holdings[0]["symbol"] == "QQQ"
    assert holdings[0]["name"] == "Invesco QQQ"
    assert holdings[0]["value"] == 7500.0
    assert holdings[0]["allocation_pct"] == 20.0
    summary = ADAPTER._summarise(holdings, accounts_count=0, source="holdings")
    assert summary["total_value"] == 7500.0


def test_holdings_enriched_fields() -> None:
    payload = {
        "holdings": [
            {
                "assetProfile": {"symbol": "QQQ", "name": "Invesco QQQ"},
                "quantity": 10,
                "marketPrice": 500,
                "valueInBaseCurrency": 5000,
                "investment": 4000,
                "allocationInPercentage": 0.5,
                "netPerformance": 1000,
                "netPerformancePercent": 0.25,
                "dateOfFirstActivity": "2024-01-05",
                "dividend": 3.0,
                "currency": "USD",
            }
        ]
    }
    holding = ADAPTER._parse_holdings_payload(payload)[0]
    assert holding["cost_per_share"] == 400.0
    assert holding["unrealized_pnl"] == 1000.0
    assert holding["unrealized_pnl_pct"] == 25.0
    assert holding["first_activity_date"] == "2024-01-05"
    assert abs(holding["dividend_yield_pct"] - 0.6) < 1e-9


def test_dividend_history_and_merge(monkeypatch) -> None:
    adapter = GhostfolioAdapter(base_url="http://ghostfolio.local", token="z" * 32)
    monkeypatch.setattr(
        adapter,
        "get_holdings",
        lambda: {
            "holdings": [
                {
                    "assetProfile": {"symbol": "QQQ"},
                    "quantity": 10,
                    "marketPrice": 500,
                    "valueInBaseCurrency": 5000,
                    "investment": 4000,
                    "allocationInPercentage": 0.5,
                }
            ]
        },
    )
    monkeypatch.setattr(
        adapter,
        "get_export",
        lambda: {
            "activities": [
                {
                    "type": "DIVIDEND",
                    "symbol": "QQQ",
                    "date": "2025-06-20",
                    "unitPrice": 0.7,
                    "quantity": 10,
                },
                {
                    "type": "DIVIDEND",
                    "symbol": "QQQ",
                    "date": "2026-03-20",
                    "unitPrice": 0.8,
                    "quantity": 10,
                },
            ]
        },
    )
    summary = adapter.get_portfolio_summary(include_dividends=True)
    qqq = summary["holdings"][0]
    assert qqq["last_dividend_date"] == "2026-03-20"
    assert qqq["last_dividend_per_share"] == 0.8
    assert qqq["dividends_total"] == 15.0


def test_summary_totals() -> None:
    holdings = [
        {"symbol": "A", "quantity": 1, "value": 150.0, "investment": 100.0, "unrealized_pnl": 50.0},
        {"symbol": "B", "quantity": 1, "value": 50.0, "investment": 100.0, "unrealized_pnl": -50.0},
    ]
    summary = ADAPTER._summarise(holdings, accounts_count=0, source="holdings")
    assert summary["total_value"] == 200.0
    assert summary["total_cost"] == 200.0
    assert summary["total_pnl"] == 0.0
    assert summary["total_pnl_pct"] == 0.0


def test_holdings_payload_handles_wrapped_root() -> None:
    payload = {"data": {"holdings": {"MSFT": {"symbol": "MSFT", "quantity": 1, "price": 100}}}}
    holdings = ADAPTER._parse_holdings_payload(payload)
    assert holdings[0]["symbol"] == "MSFT"
    assert holdings[0]["value"] == 100.0


def test_summary_falls_back_to_activities_when_holdings_empty(monkeypatch) -> None:
    adapter = GhostfolioAdapter(base_url="http://ghostfolio.local", token="y" * 32)
    monkeypatch.setattr(adapter, "get_holdings", lambda: {"holdings": {}})
    monkeypatch.setattr(
        adapter,
        "get_export",
        lambda: {
            "accounts": {"a": {"name": "main"}},
            "activities": [{"symbol": "AAPL", "type": "BUY", "quantity": 3, "currency": "USD"}],
        },
    )
    summary = adapter.get_portfolio_summary()
    assert summary["source"] == "activities"
    assert summary["holdings_count"] == 1
    assert summary["accounts_count"] == 1


def test_holdings_value_derived_from_price_when_absent() -> None:
    payload = {"holdings": [{"symbol": "BTC-USD", "quantity": 0.5, "price": 60000}]}
    holdings = ADAPTER._parse_holdings_payload(payload)
    assert holdings[0]["value"] == 30000.0


def test_activities_fallback_handles_flat_symbol_without_crashing() -> None:
    """The old code raised UnboundLocalError here."""

    activities = [
        {"symbol": "MSFT", "type": "BUY", "quantity": 5, "currency": "USD"},
        {"symbol": "MSFT", "type": "SELL", "quantity": 2},
        {"SymbolProfile": {"symbol": "NVDA", "name": "Nvidia"}, "type": "BUY", "quantity": 3},
    ]
    holdings = ADAPTER._parse_activities(activities)
    by_symbol = {h["symbol"]: h for h in holdings}
    assert by_symbol["MSFT"]["quantity"] == 3  # 5 - 2
    assert "NVDA" in by_symbol


def test_portfolio_context_for_match_and_miss() -> None:
    holdings = [{"symbol": "AAPL", "quantity": 10, "allocation_pct": 12.5}]
    matched = portfolio_context_for("AAPL", holdings)
    assert matched["ghostfolio_connected"] is True
    assert matched["holding"]["symbol"] == "AAPL"
    assert "12.50%" in matched["note"]

    missing = portfolio_context_for("TSLA", holdings)
    assert missing["holding"] is None
    assert "未持有" in missing["note"]


def test_canonical_symbol_aligns_crypto_and_keeps_equities() -> None:
    from app.data.symbols import canonical_symbol

    assert canonical_symbol("BTC-USD") == canonical_symbol("BITCOIN") == "BTC"
    assert canonical_symbol("bitcoin") == "BTC"
    assert canonical_symbol("ETH-USD") == canonical_symbol("ETHEREUM") == "ETH"
    assert canonical_symbol("BNB-USD") == canonical_symbol("BINANCECOIN") == "BNB"
    assert canonical_symbol("BTCUSDT") == "BTC"
    assert canonical_symbol("QQQ") == "QQQ"
    assert canonical_symbol(None) == ""


def test_portfolio_context_matches_crypto_alias() -> None:
    holdings = [{"symbol": "BITCOIN", "name": "Bitcoin", "quantity": 0.15, "allocation_pct": 11.3}]
    context = portfolio_context_for("BTC-USD", holdings)
    assert context["holding"]["symbol"] == "BITCOIN"
    assert "11.30%" in context["note"]


def test_portfolio_context_when_ghostfolio_unconfigured() -> None:
    context = portfolio_context_for("AAPL", None)
    assert context["ghostfolio_connected"] is False
    assert "不受影响" in context["note"]


def test_signal_persists_portfolio_context(db_session, sample_bars) -> None:
    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import (
        Asset,
        MarketDataSeries,
        MarketDataSource,
        Strategy,
        StrategyVersion,
    )
    from app.simulation.signal_engine import scan_series

    dsl = {
        "schema_version": "1.0",
        "strategy": {"id": "gf", "name": "GF", "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
        "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
        "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
    }
    strategy = Strategy(name="GF", slug="gf-snap")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json=dsl,
        immutable_hash="g" * 64,
        validation_status="valid",
        is_current=True,
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="GF", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="gf-src", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    upsert_bars(db_session, series, frame_to_bars(sample_bars))
    db_session.commit()

    signal = scan_series(db_session, version, series)
    # Ghostfolio is unconfigured in tests, so the context records that honestly.
    assert signal.portfolio_context_json is not None
    assert signal.portfolio_context_json["ghostfolio_connected"] is False
