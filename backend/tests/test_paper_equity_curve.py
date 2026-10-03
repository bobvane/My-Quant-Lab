"""The equity curve is a history, not today's baseline drawn backwards (ADR-108).

``docs/12_API_SPEC.md`` promised an equity curve for the paper account while the
endpoint only returned a snapshot, and the obvious implementation -- start from the
net deposits the account holds *today*, then add every closed trade -- silently
rewrites its own history every time money enters or leaves the account (ADR-066 moves
the baseline with the flow). These tests hold the curve to what it claims: points in
time order, the first point at the opening cash, the last point at
``net_deposits + realized P&L``, and a deposit that shows up as a step rather than as
performance.
"""

from __future__ import annotations

import datetime as dt
import pathlib

from app.api.schemas import PaperAccountCreate
from app.domain.models import Asset, PaperTrade

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _account(client, cash: float = 10_000.0) -> int:
    created = client.post(
        "/api/v1/paper/accounts",
        json=PaperAccountCreate(name="Curve Account", initial_cash=cash).model_dump(),
    )
    assert created.status_code == 201
    return created.json()["id"]


def _asset(db_session, symbol: str = "DEMO-CURVE") -> int:
    asset = Asset(symbol=symbol, asset_class="crypto", currency="USD")
    db_session.add(asset)
    db_session.flush()
    return asset.id


def _closed_trade(
    db_session, account_id: int, asset_id: int, when: dt.datetime, pnl: float
) -> None:
    db_session.add(
        PaperTrade(
            account_id=account_id,
            asset_id=asset_id,
            direction="long",
            entry_time=when - dt.timedelta(days=1),
            exit_time=when,
            entry_price=100,
            exit_price=100 + pnl / 10,
            quantity=10,
            pnl=pnl,
        )
    )
    db_session.commit()


def _curve(client, account_id: int) -> dict:
    response = client.get(f"/api/v1/paper/accounts/{account_id}/equity")
    assert response.status_code == 200
    return response.json()


def _steps(curve: list[dict]) -> list[float]:
    return [
        round(curve[index + 1]["equity"] - curve[index]["equity"], 4)
        for index in range(len(curve) - 1)
    ]


def test_the_curve_starts_at_the_opening_cash_and_ends_at_the_final_equity(
    client, db_session
) -> None:
    account_id = _account(client)
    asset_id = _asset(db_session)
    first = dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=5)
    _closed_trade(db_session, account_id, asset_id, first, 200)
    _closed_trade(db_session, account_id, asset_id, first + dt.timedelta(minutes=1), -50)

    body = _curve(client, account_id)
    curve = body["equity_curve"]
    assert [point["equity"] for point in curve] == [10_000.0, 10_200.0, 10_150.0]
    assert body["realized_pnl"] == 150.0
    assert body["equity_curve"][0]["equity"] == 10_000.0
    # The published curve and the published metrics have to agree at the right edge.
    performance = client.get(f"/api/v1/paper/accounts/{account_id}/performance").json()
    assert curve[-1]["equity"] == performance["final_equity"] == 10_150.0
    timestamps = [point["timestamp"] for point in curve]
    assert timestamps == sorted(timestamps)
    assert "重置" in body["curve_note"]


def test_a_deposit_is_a_step_in_the_curve_and_not_a_gain(client, db_session) -> None:
    account_id = _account(client)
    asset_id = _asset(db_session)
    _closed_trade(
        db_session, account_id, asset_id, dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=5), 300
    )
    funded = client.post(f"/api/v1/paper/accounts/{account_id}/fund", json={"amount": 5_000})
    assert funded.status_code == 200

    body = _curve(client, account_id)
    curve = body["equity_curve"]
    assert body["net_deposits"] == 15_000.0
    assert body["realized_pnl"] == 300.0
    # The account opened with 10 000, and the 5 000 arrived later: a curve drawn from
    # today's baseline would start at 15 000 and lose the deposit from its history.
    assert curve[0]["equity"] == 10_000.0
    assert _steps(curve) == [5_000.0, 300.0]
    assert curve[-1]["equity"] == body["net_deposits"] + body["realized_pnl"] == 15_300.0


def test_a_withdrawal_lowers_the_baseline_instead_of_the_pnl(client, db_session) -> None:
    account_id = _account(client)
    asset_id = _asset(db_session)
    _closed_trade(
        db_session, account_id, asset_id, dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=5), 300
    )
    taken = client.post(f"/api/v1/paper/accounts/{account_id}/fund", json={"amount": -2_000})
    assert taken.status_code == 200

    body = _curve(client, account_id)
    assert body["net_deposits"] == 8_000.0
    assert body["realized_pnl"] == 300.0
    assert _steps(body["equity_curve"]) == [-2_000.0, 300.0]


def test_the_spec_names_the_fields_the_curve_publishes() -> None:
    """docs/12 promised a curve long before the endpoint returned one (ADR-108)."""
    spec = (REPO_ROOT / "docs" / "12_API_SPEC.md").read_text(encoding="utf-8")
    endpoint = spec.split("`GET /paper/accounts/{account_id}/equity`", 1)[1].split("## ", 1)[0]
    for field in ("equity_curve", "curve_note"):
        assert field in endpoint, (
            f"docs/12 describes the equity endpoint without naming {field}: the promise and "
            "the payload have to be the same thing (ADR-108)"
        )


def test_a_reset_starts_the_curve_over(client, db_session) -> None:
    account_id = _account(client)
    asset_id = _asset(db_session)
    _closed_trade(
        db_session, account_id, asset_id, dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=5), 300
    )
    reset = client.post(
        f"/api/v1/paper/accounts/{account_id}/reset", params={"initial_cash": 8_000}
    )
    assert reset.status_code == 200

    body = _curve(client, account_id)
    assert body["net_deposits"] == 8_000.0
    assert body["realized_pnl"] == 0.0
    assert body["trades_count"] == 0
    # The reset deleted the trades and moved the baseline, so nothing before it can be
    # drawn any more: one point, the new opening cash.
    assert [point["equity"] for point in body["equity_curve"]] == [8_000.0]
