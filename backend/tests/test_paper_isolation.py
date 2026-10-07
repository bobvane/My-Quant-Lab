"""ADR-006: paper trading can never reach the real portfolio.

The paper account is the one place in the product that pretends to trade, and the whole
point of the pretence is that it stays inside its own tables: it must not be able to write
anywhere near Ghostfolio, and its account view must still be computable while Ghostfolio is
unconfigured or unreachable.

Reading the code is not evidence, so the boundary is asserted three ways: the paper modules
never name the adapter as code (a docstring may mention it), the adapter itself has no write
path beyond its one-time auth exchange, and the whole paper flow is driven with a tripwire
standing in for the adapter so that constructing it at all fails the test. This mirrors
``test_api.py::test_no_broker_endpoint_exists``: the guarantee is the absence of a path.
"""

from __future__ import annotations

import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from app.api.main import create_app
from app.data.market_data_repo import frame_to_bars, upsert_bars
from app.domain.models import (
    Asset,
    MarketDataSeries,
    MarketDataSource,
    PaperAccount,
    PaperPosition,
)

APP = Path(__file__).resolve().parents[1] / "app"

#: The modules that make up the paper feature: its API surface and its engine.
PAPER_MODULES = ("api/routers/paper.py", "simulation/paper_engine.py")

#: Reading the real portfolio means importing this module or naming one of these.
PORTFOLIO_MODULE = "app.data.ghostfolio"
PORTFOLIO_NAMES = frozenset({"GhostfolioAdapter", "GhostfolioError"})

BAR_START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _tree(name: str) -> ast.Module:
    return ast.parse((APP / name).read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    """Every module path imported by ``tree``, however it was imported."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _named(tree: ast.Module) -> set[str]:
    """Identifiers and attribute names appearing as *code*, never inside a string."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _called_methods(tree: ast.Module) -> set[str]:
    """Methods called on an object: ``client.get(...)`` contributes ``get``."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


@pytest.mark.parametrize("name", PAPER_MODULES)
def test_a_paper_module_never_imports_the_real_portfolio(name: str) -> None:
    assert PORTFOLIO_MODULE not in _imported_modules(_tree(name))


@pytest.mark.parametrize("name", PAPER_MODULES)
def test_a_paper_module_never_names_the_ghostfolio_adapter(name: str) -> None:
    assert not _named(_tree(name)) & PORTFOLIO_NAMES


@pytest.mark.parametrize("name", PAPER_MODULES)
def test_a_paper_module_speaks_no_http(name: str) -> None:
    """No HTTP client in the paper layer: its only inputs are its tables and stored bars."""

    source = (APP / name).read_text(encoding="utf-8")

    assert "httpx" not in source
    assert "requests" not in source


def test_the_portfolio_adapter_has_no_write_path() -> None:
    """Ghostfolio is read-only here: its only POST is the token exchange."""

    source = (APP / "data/ghostfolio.py").read_text(encoding="utf-8")
    calls = _called_methods(ast.parse(source))

    assert "get" in calls
    assert source.count("client.post(") == 1
    assert "auth/anonymous" in source
    for verb in ("put", "patch", "delete"):
        assert f"client.{verb}(" not in source


def test_no_route_carries_a_paper_order_to_the_real_portfolio() -> None:
    """Paper endpoints exist, and none of them is a broker or portfolio bridge."""

    paths = {path for path in create_app().openapi()["paths"] if "/paper" in path}

    assert paths
    for path in paths:
        assert "ghostfolio" not in path
        assert "broker" not in path


def test_no_portfolio_write_endpoint_exists(client) -> None:
    for path in (
        "/api/v1/paper/accounts/1/ghostfolio",
        "/api/v1/ghostfolio/orders",
        "/api/v1/ghostfolio/activities",
    ):
        assert client.post(path, json={}).status_code in {404, 405}


def test_the_paper_flow_runs_without_the_real_portfolio(client, db_session, monkeypatch):
    """The tripwire makes "the totals never reach the adapter" an executable claim."""

    from app.data import ghostfolio

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("paper trading constructed the Ghostfolio adapter (ADR-006)")

    monkeypatch.setattr(ghostfolio, "GhostfolioAdapter", _tripwire)

    asset = Asset(symbol="ISOLATED", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="src-isolated", base_url="test")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    frame = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0], "volume": [1_000.0]},
        index=pd.date_range(start=BAR_START, periods=1, freq="D"),
    )
    upsert_bars(db_session, series, frame_to_bars(frame))
    account = PaperAccount(name="Isolated", initial_cash=1_000, cash=Decimal("900"))
    db_session.add(account)
    db_session.flush()
    db_session.add(
        PaperPosition(
            account_id=account.id,
            asset_id=asset.id,
            quantity=Decimal("1"),
            avg_cost=Decimal("100"),
            realized_pnl=Decimal("0"),
        )
    )
    db_session.commit()

    body = client.get(f"/api/v1/paper/accounts/{account.id}").json()
    row = client.get(f"/api/v1/paper/accounts/{account.id}/positions").json()[0]

    assert body["market_value"] == 100.0
    assert body["total_equity"] == 1_000.0
    assert row["mark_price"] == 100.0
    assert client.post(f"/api/v1/paper/accounts/{account.id}/reset").status_code == 200
