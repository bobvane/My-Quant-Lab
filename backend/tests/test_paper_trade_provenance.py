"""ADR-204: a paper fill says what it cost and where it came from.

A trade stores the order that produced it (``PaperTrade.order_id``) and an order stores
the signal it was executed for (``PaperOrder.signal_id``), so the trade list can publish
each row's **own** provenance: the order, the signal, the fees and the slippage. That is
a join over stored rows, not a guess about which strategy a fill "must have" belonged to
-- ADR-114's account-level attribution is refined, not replaced, and a fill that came
from no signal says ``signal_id: null`` instead of borrowing a plausible one.

Two of these fields were already in the database and still missing from the API, which is
why the paper page could only print 「未知」 for fees and slippage; ``r_multiple`` is the
opposite case -- the column exists, the paper engine never writes it, so it is published
as a real ``None`` (「未知」) rather than a fabricated 0 (ADR-112).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import event

from app.api.routers.paper import _trade_payloads
from app.domain.models import (
    Asset,
    PaperAccount,
    PaperOrder,
    PaperTrade,
    Signal,
    Strategy,
    StrategyVersion,
)

BAR = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)


def _seed(db) -> tuple[PaperAccount, Asset, Signal]:
    strategy = Strategy(name="Provenance", slug="provenance")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={},
        immutable_hash="c" * 64,
        validation_status="valid",
    )
    asset = Asset(symbol="PROV", asset_class="stock")
    db.add_all([version, asset])
    db.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=BAR,
        state="BUY",
        direction="LONG",
        price_reference=Decimal("100"),
        feature_snapshot_hash="p" * 64,
        data_source="test",
    )
    account = PaperAccount(name="Provenance", initial_cash=10_000, cash=10_000)
    db.add_all([signal, account])
    db.flush()
    return account, asset, signal


def _order(db, account: PaperAccount, asset: Asset, *, signal_id: int | None) -> PaperOrder:
    order = PaperOrder(
        account_id=account.id,
        signal_id=signal_id,
        asset_id=asset.id,
        side="BUY",
        quantity=Decimal("10"),
        status="filled",
        fill_price=Decimal("100"),
        fees=Decimal("1.5"),
        slippage=Decimal("0.25"),
    )
    db.add(order)
    db.flush()
    return order


def _trade(db, account: PaperAccount, asset: Asset, *, order_id: int | None) -> PaperTrade:
    trade = PaperTrade(
        account_id=account.id,
        order_id=order_id,
        asset_id=asset.id,
        direction="LONG",
        entry_time=BAR,
        entry_price=Decimal("100"),
        quantity=Decimal("10"),
        fees=Decimal("1.5"),
        slippage=Decimal("0.25"),
    )
    db.add(trade)
    db.flush()
    return trade


def test_a_trade_names_the_order_and_the_signal_behind_it(db_session) -> None:
    account, asset, signal = _seed(db_session)
    order = _order(db_session, account, asset, signal_id=signal.id)
    trade = _trade(db_session, account, asset, order_id=order.id)

    (payload,) = _trade_payloads(db_session, [trade], with_account=True)

    assert payload["order_id"] == order.id
    assert payload["signal_id"] == signal.id
    assert payload["fees"] == 1.5
    assert payload["slippage"] == 0.25
    assert payload["account_id"] == account.id
    # The engine does not write a paper R multiple: unknown, never 0.
    assert payload["r_multiple"] is None


def test_a_fill_that_came_from_no_signal_says_so(db_session) -> None:
    account, asset, signal = _seed(db_session)

    # An order placed without a signal, and a trade with no order at all: neither may
    # borrow the signal that happens to be in the same account.
    plain_order = _order(db_session, account, asset, signal_id=None)
    trade_without_signal = _trade(db_session, account, asset, order_id=plain_order.id)
    trade_without_order = _trade(db_session, account, asset, order_id=None)

    payloads = _trade_payloads(
        db_session, [trade_without_signal, trade_without_order], with_account=True
    )

    assert [row["signal_id"] for row in payloads] == [None, None]
    assert payloads[0]["order_id"] == plain_order.id
    assert payloads[1]["order_id"] is None
    assert signal.id is not None


def test_the_account_level_payload_leaves_out_its_own_address(db_session) -> None:
    account, asset, signal = _seed(db_session)
    order = _order(db_session, account, asset, signal_id=signal.id)
    trade = _trade(db_session, account, asset, order_id=order.id)

    (payload,) = _trade_payloads(db_session, [trade], with_account=False)

    assert "account_id" not in payload
    assert payload["signal_id"] == signal.id


def test_the_whole_page_costs_one_order_lookup(db_session) -> None:
    account, asset, signal = _seed(db_session)
    trades = []
    for _ in range(5):
        order = _order(db_session, account, asset, signal_id=signal.id)
        trades.append(_trade(db_session, account, asset, order_id=order.id))

    bind = db_session.get_bind()
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if "paper_orders" in statement:
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", _record)
    try:
        payloads = _trade_payloads(db_session, trades, with_account=True)
    finally:
        event.remove(bind, "before_cursor_execute", _record)

    assert len(statements) == 1, statements
    assert [row["signal_id"] for row in payloads] == [signal.id] * 5
