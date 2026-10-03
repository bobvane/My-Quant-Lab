"""What a paper account's return is measured against (P1) and over (P4).

Two ways the performance endpoint used to describe money that was not the account's
investment result:

* P1: every ratio was computed on ``net_deposits`` — the *current* net deposits —
  so a withdrawal raised the reported return (1,000 earned on 10,000 of capital read
  as +16.67% once 4,000 was taken out) and a deposit diluted it. The equity endpoint
  already replays funding as the step it is (ADR-108); these tests make the
  performance endpoint agree with it.
* P4: the metric series had one point per trade, and ``compute_metrics`` turned a
  point into a day (1d = 252/year), so two trades were "0.0119 years" and a +2%
  total annualised to a four-digit number. Annualisation now uses the time the
  trades actually span.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.models import Asset, AuditLog, PaperAccount, PaperTrade


def _account(client, *, cash: float = 10_000, name: str = "Perf PA") -> dict:
    response = client.post("/api/v1/paper/accounts", json={"name": name, "initial_cash": cash})
    assert response.status_code == 201
    return response.json()


def _trade(
    db,
    *,
    account_id: int,
    pnl: float,
    entry: dt.datetime,
    exit: dt.datetime,
    cash_after: float,
    symbol: str = "PERF",
) -> None:
    """Book a closed trade the way the engine would, and leave the cash it left."""

    asset = db.query(Asset).filter(Asset.symbol == symbol).one_or_none()
    if asset is None:
        asset = Asset(symbol=symbol, asset_class="stock")
        db.add(asset)
        db.flush()
    db.add(
        PaperTrade(
            account_id=account_id,
            asset_id=asset.id,
            direction="LONG",
            entry_time=entry,
            entry_price=10.0,
            exit_time=exit,
            exit_price=11.0,
            quantity=1.0,
            pnl=pnl,
        )
    )
    account = db.get(PaperAccount, account_id)
    account.cash = cash_after
    db.commit()


def _fund(client, account_id: int, amount: float) -> dict:
    response = client.post(f"/api/v1/paper/accounts/{account_id}/fund", json={"amount": amount})
    assert response.status_code == 200
    return response.json()


def _backdate(db, account_id: int, moment: dt.datetime) -> None:
    """Start the account's life at a chosen moment so fixture trades sit inside it.

    The audit replay only counts events after the account was created, and the API
    stamps that creation with the wall clock.
    """
    account = db.get(PaperAccount, account_id)
    account.created_at = moment
    db.commit()


def _fund_at(db, *, account_id: int, amount: float, moment: dt.datetime, cash_after: float) -> None:
    """Move money the way `/fund` does, at a chosen moment.

    The endpoint stamps the audit event with the wall clock, which cannot land between
    two fixture trades; the replay reads the audit row, so the fixture writes the row.
    """
    account = db.get(PaperAccount, account_id)
    account.cash = Decimal(str(cash_after))
    account.initial_cash = Decimal(str(account.initial_cash)) + Decimal(str(amount))
    db.add(
        AuditLog(
            event_type="paper_account_funded",
            entity_type="paper_account",
            entity_id=str(account_id),
            action="fund",
            payload_json={"amount": str(Decimal(str(amount))), "cash": str(cash_after)},
            created_at=moment,
        )
    )
    db.commit()


def _perf(client, account_id: int) -> dict:
    response = client.get(f"/api/v1/paper/accounts/{account_id}/performance")
    assert response.status_code == 200
    return response.json()


def test_a_withdrawal_does_not_raise_the_return_the_trades_earned(client, db_session) -> None:
    account = _account(client, cash=10_000)
    _trade(
        db_session,
        account_id=account["id"],
        pnl=1_000.0,
        entry=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 10, tzinfo=dt.UTC),
        cash_after=11_000.0,
    )
    moved = _fund(client, account["id"], -4_000)
    assert moved["net_deposits"] == pytest.approx(6_000)

    body = _perf(client, account["id"])

    # The money is $6,000 of deposits plus $1,000 of profit; the *return* is what the
    # capital at work earned, and 4,000 leaving the account did not earn anything.
    assert body["net_deposits"] == pytest.approx(6_000)
    assert body["final_equity"] == pytest.approx(7_000)
    assert body["metrics"]["total_return"] == pytest.approx(0.10, rel=1e-9)


def test_a_deposit_after_the_trades_is_not_a_return(client, db_session) -> None:
    account = _account(client, cash=10_000)
    _trade(
        db_session,
        account_id=account["id"],
        pnl=1_000.0,
        entry=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 10, tzinfo=dt.UTC),
        cash_after=11_000.0,
    )
    moved = _fund(client, account["id"], 5_000)
    assert moved["net_deposits"] == pytest.approx(15_000)

    body = _perf(client, account["id"])

    # Adding money does not make the earlier 10% smaller.
    assert body["metrics"]["total_return"] == pytest.approx(0.10, rel=1e-9)


def test_each_trade_is_measured_against_the_money_it_ran_on(client, db_session) -> None:
    account = _account(client, cash=10_000)
    _backdate(db_session, account["id"], dt.datetime(2025, 12, 31, tzinfo=dt.UTC))
    _trade(
        db_session,
        account_id=account["id"],
        pnl=1_000.0,
        entry=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 10, tzinfo=dt.UTC),
        cash_after=11_000.0,
    )
    _fund_at(
        db_session,
        account_id=account["id"],
        amount=10_000.0,
        moment=dt.datetime(2026, 1, 20, tzinfo=dt.UTC),
        cash_after=21_000.0,
    )
    _trade(
        db_session,
        account_id=account["id"],
        pnl=2_100.0,
        entry=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 2, 10, tzinfo=dt.UTC),
        cash_after=23_100.0,
        symbol="PERF2",
    )

    body = _perf(client, account["id"])

    # +10% on 10,000, then +10% on 21,000: time-weighted return is 21%, not
    # 3,100 / 20,000 = 15.5% and not 3,100 / 10,000 either.
    assert body["metrics"]["total_return"] == pytest.approx(0.21, rel=1e-9)


def test_a_withdrawal_is_not_a_drawdown(client, db_session) -> None:
    account = _account(client, cash=10_000)
    _trade(
        db_session,
        account_id=account["id"],
        pnl=1_000.0,
        entry=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 10, tzinfo=dt.UTC),
        cash_after=11_000.0,
    )
    _trade(
        db_session,
        account_id=account["id"],
        pnl=-500.0,
        entry=dt.datetime(2026, 1, 15, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 20, tzinfo=dt.UTC),
        cash_after=10_500.0,
        symbol="PERF2",
    )
    _fund(client, account["id"], -8_000)

    body = _perf(client, account["id"])

    # The account lost 500 of 11,000 after its first trade: -4.55%, not the -16.7%
    # that the withdrawn 8,000 used to look like.
    assert body["metrics"]["max_drawdown"] == pytest.approx(-500.0 / 11_000.0, rel=1e-9)


def test_annualisation_uses_the_elapsed_time_not_the_trade_count(client, db_session) -> None:
    account = _account(client, cash=10_000)
    _trade(
        db_session,
        account_id=account["id"],
        pnl=100.0,
        entry=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        cash_after=10_100.0,
    )
    _trade(
        db_session,
        account_id=account["id"],
        pnl=101.0,
        entry=dt.datetime(2026, 7, 2, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 7, 3, tzinfo=dt.UTC),
        cash_after=10_201.0,
        symbol="PERF2",
    )

    body = _perf(client, account["id"])

    # Two trades, +1% each, half a year apart: the honest annualisation of +2.01%
    # over 183 days is ~4%, and the trade count is not a calendar.
    years = 183 / 365.25
    expected = (1.0201) ** (1.0 / years) - 1.0
    assert body["metrics"]["cagr"] == pytest.approx(expected, rel=1e-6)
    assert body["metrics"]["cagr"] < 0.1


def test_cagr_is_withheld_when_no_time_passed(client, db_session) -> None:
    moment = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    account = _account(client, cash=10_000)
    _trade(
        db_session,
        account_id=account["id"],
        pnl=100.0,
        entry=moment,
        exit=moment,
        cash_after=10_100.0,
    )

    body = _perf(client, account["id"])

    assert body["metrics"]["total_return"] == pytest.approx(0.01, rel=1e-9)
    assert body["metrics"]["cagr"] is None
    assert "no period" in " ".join(body["metric_notes"])


def test_the_account_list_publishes_the_realized_result(client, db_session) -> None:
    """P&L of an account is what its closed trades earned, not the cash it has left.

    A full-size buy spends the whole balance and leaves a position on the books, so
    `cash - net_deposits` — what the paper card and the dashboard printed — showed a
    brand-new position as -100% (ADR-124).
    """
    account = _account(client, cash=10_000)
    _trade(
        db_session,
        account_id=account["id"],
        pnl=1_000.0,
        entry=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        exit=dt.datetime(2026, 1, 10, tzinfo=dt.UTC),
        cash_after=1_000.0,
    )

    rows = client.get("/api/v1/paper/accounts").json()
    row = next(item for item in rows if item["id"] == account["id"])
    detail = client.get(f"/api/v1/paper/accounts/{account['id']}").json()

    assert row["realized_pnl"] == pytest.approx(1_000)
    assert detail["realized_pnl"] == pytest.approx(1_000)
    # The cash is where the money is; the 9,000 it spent is not a loss.
    assert row["cash"] == pytest.approx(1_000)
    assert row["cash"] - row["net_deposits"] == pytest.approx(-9_000)
