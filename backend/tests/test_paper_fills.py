"""What price a paper fill uses, and what moment it claims to have happened (P2, P3).

Two defects of the same family — a fill that describes something other than what
happened:

* P2: an order sized to spend exactly the account's cash was rejected as
  ``insufficient cash`` because ``quantity * fill_price + fees`` came out one unit
  in the 28th significant digit above the cash it was derived from. The rejection
  rate depends on the price, which is why the existing tests (round numbers) never
  saw it.
* P3: the fill price comes from the bar the signal was computed on, but the fill
  time was ``datetime.now()``. A trade booked off a January bar was stamped with
  today, and the equity replay orders trades by that stamp — so executing an old
  signal inserted a trade into the middle of history.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain.models import (
    Asset,
    PaperAccount,
    PaperOrder,
    PaperTrade,
    Signal,
    Strategy,
    StrategyVersion,
)
from app.simulation.paper_engine import PaperError, PaperExecutionSettings, execute_signal

BAR_TIME = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)


def _seed(
    db,
    *,
    cash: float = 10_000,
    price: float = 100.0,
    state: str = "BUY",
    bar_time: dt.datetime = BAR_TIME,
):
    strategy = Strategy(name="Fills", slug="fills-strat")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="f" * 64
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol="FILLS", asset_class="stock")
    db.add(asset)
    db.flush()
    account = PaperAccount(name="Fill PA", initial_cash=cash, cash=cash)
    db.add(account)
    db.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=bar_time,
        state=state,
        direction="LONG",
        price_reference=price,
        triggered_rules_json=["rule"],
        feature_snapshot_hash="f" * 64,
        data_source="test",
    )
    db.add(signal)
    db.commit()
    return account, signal, asset


def _utc(moment: dt.datetime) -> dt.datetime:
    """SQLite hands back naive timestamps for tz-aware columns."""

    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)


# Cash levels and prices whose Decimal division lands on a repeating fraction: the
# all-in order spends the whole balance, and `quantity * fill_price + fees` used to
# come back one ulp above it (P2).
@pytest.mark.parametrize(
    ("cash", "price"),
    [
        (5_000.0, 199.99),
        (7_000.0, 2.71828),
        (999.99, 0.07),
        (999.99, 91.7),
        (12_345.67, 45.67),
        (14_999.37, 12.34),
        (8_888.88, 333.33),
    ],
)
def test_a_full_size_buy_spends_the_whole_balance(db_session, cash: float, price: float) -> None:
    account, signal, _asset = _seed(db_session, cash=cash, price=price)

    result = execute_signal(db_session, account, signal, settings=PaperExecutionSettings())

    assert result["side"] == "BUY"
    assert float(result["quantity"]) > 0
    # All-in means all-in: a refusal here is the bug, and a leftover cent would mean
    # the order was silently downsized.
    assert float(account.cash) == pytest.approx(0.0, abs=1e-6)


def test_a_buy_that_really_cannot_be_afforded_is_still_refused(db_session) -> None:
    """The rounding fix must not turn the guard into a rubber stamp."""

    account, _signal, _asset = _seed(db_session, cash=1_000.0, price=100.0)
    account.cash = 0
    db_session.commit()

    with pytest.raises(PaperError):
        execute_signal(db_session, account, _signal, settings=PaperExecutionSettings())


def test_a_fill_is_timed_by_the_bar_its_signal_came_from(db_session) -> None:
    account, signal, asset = _seed(db_session, cash=10_000, price=100.0)

    execute_signal(db_session, account, signal, settings=PaperExecutionSettings())

    order = db_session.query(PaperOrder).one()
    trade = db_session.query(PaperTrade).one()
    assert _utc(order.filled_at) == BAR_TIME
    assert _utc(trade.entry_time) == BAR_TIME

    sell = Signal(
        strategy_version_id=signal.strategy_version_id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 5, tzinfo=dt.UTC),
        state="SELL",
        direction="FLAT",
        price_reference=110.0,
        feature_snapshot_hash="g" * 64,
        data_source="test",
    )
    db_session.add(sell)
    db_session.commit()

    execute_signal(db_session, account, sell, settings=PaperExecutionSettings())

    db_session.refresh(trade)
    assert _utc(trade.exit_time) == dt.datetime(2026, 1, 5, tzinfo=dt.UTC)
    assert _utc(trade.entry_time) < _utc(trade.exit_time)


def test_an_explicit_moment_still_overrides_the_bar(db_session) -> None:
    """Callers that know the moment (tests, replays) keep control of it."""

    account, signal, _asset = _seed(db_session, cash=10_000, price=100.0)
    explicit = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)

    execute_signal(db_session, account, signal, settings=PaperExecutionSettings(), now=explicit)

    trade = db_session.query(PaperTrade).one()
    assert _utc(trade.entry_time) == explicit
