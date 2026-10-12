"""One signal, one order: repeating an execution answers instead of filling again (ADR-221).

The paper engine used to read ``paper_orders`` for the signal, find nothing and then write
a row. Two callers doing that at the same time both read nothing and both wrote, so one
signal could end up with two orders, two cash movements and two ledger rows — and a
partial close could be deducted from the position twice. The rule pinned down here is:

* the same signal produces exactly one order, whatever the caller asks for the second time;
* a repeat is answered with the order the first call created, and writes nothing at all;
* the database, not the read-then-write check, is what refuses the second writer.

That last point is why these tests go through separate sessions and real threads instead of
just calling ``execute_signal`` twice: a check written in Python cannot see a row another
transaction has not committed yet. The lost-race test below blinds the Python check on
purpose to prove the unique index holds the line anyway.
"""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.db import Base
from app.domain.models import (
    Asset,
    AuditLog,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    Signal,
    Strategy,
    StrategyVersion,
)
from app.simulation import paper_engine
from app.simulation.paper_engine import PaperExecutionSettings, execute_signal

# The same assertions have to hold on both dialects: SQLite cannot express every
# constraint natively, and the unique index this file leans on is exactly the kind of
# thing that can be true on one and missing on the other (ADR-212, ADR-221).
pytestmark = pytest.mark.postgres

BAR = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
SETTINGS = PaperExecutionSettings()


def _hash(seed: str) -> str:
    return (seed * 64)[:64]


def _seed(db, *, tag: str, cash: float = 10_000, price: float = 100.0):
    """A funded account and one BUY signal.

    ``tag`` keeps the unique columns (strategy slug, asset symbol) apart between tests:
    leftovers from a test that does not wipe the schema must not make the next one fail.
    """

    strategy = Strategy(name=f"Idempotent {tag}", slug=f"idem-{tag}")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={},
        immutable_hash=_hash(tag),
        validation_status="valid",
    )
    asset = Asset(symbol=f"IDEM{tag.upper()}", asset_class="stock")
    db.add_all([version, asset])
    db.flush()
    account = PaperAccount(name=f"Idempotent {tag}", initial_cash=cash, cash=cash)
    db.add(account)
    db.flush()
    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=BAR,
        state="BUY",
        direction="LONG",
        price_reference=Decimal(str(price)),
        feature_snapshot_hash=_hash(f"{tag}-buy"),
        data_source="test",
    )
    db.add(signal)
    db.commit()
    return account, signal, asset


def _next_signal(db, signal: Signal, asset: Asset, *, state: str, days: int) -> Signal:
    """A later signal for the same asset: a different signal is a different order."""

    moment = signal.bar_timestamp + dt.timedelta(days=days)
    moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)
    row = Signal(
        strategy_version_id=signal.strategy_version_id,
        asset_id=asset.id,
        timeframe=signal.timeframe,
        bar_timestamp=moment,
        state=state,
        direction="FLAT" if state == "SELL" else "LONG",
        price_reference=signal.price_reference,
        feature_snapshot_hash=_hash(f"{state}{days}"),
        data_source="test",
    )
    db.add(row)
    db.commit()
    return row


def _ledger(db, account_id: int) -> dict[str, Any]:
    """Everything a fill can move, read back from the database.

    ``cash`` is re-read rather than taken from the session's object: the column is
    Numeric(24, 8), so an in-memory balance can differ from the stored one in the last
    places — and the stored one is the balance the account really has.
    """

    db.expire_all()
    position = db.scalar(select(PaperPosition).where(PaperPosition.account_id == account_id))
    return {
        "cash": db.scalar(select(PaperAccount.cash).where(PaperAccount.id == account_id)),
        "orders": db.query(PaperOrder).filter(PaperOrder.account_id == account_id).count(),
        "trades": db.query(PaperTrade).filter(PaperTrade.account_id == account_id).count(),
        "position": position.quantity if position is not None else None,
        "realized_pnl": position.realized_pnl if position is not None else None,
        "open_trade_quantity": db.scalar(
            select(PaperTrade.quantity)
            .where(PaperTrade.account_id == account_id, PaperTrade.exit_time.is_(None))
            .order_by(PaperTrade.id.desc())
        ),
        "audit_events": db.query(AuditLog)
        .filter(AuditLog.event_type == "paper_order_executed")
        .count(),
    }


def _orders_for(db, signal_id: int) -> list[PaperOrder]:
    return (
        db.query(PaperOrder).filter(PaperOrder.signal_id == signal_id).order_by(PaperOrder.id).all()
    )


@pytest.fixture()
def shared_engine(tmp_path, db_session):
    """An engine that two threads can write to at the same time.

    ``db_session`` cannot be used for that: its SQLite path is one shared in-memory
    connection (StaticPool), so two threads would take turns inside a single transaction
    instead of racing. PostgreSQL is used when the suite is pointed at one — that is the
    CI job, and the dialect where the concurrency claim has to hold for real — and a
    file-backed SQLite with a real busy timeout otherwise. Each thread opens its own
    session either way.
    """

    engine = db_session.get_bind()
    if engine.dialect.name != "sqlite":
        yield engine
        return

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'paper-race.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _wal(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# A repeat is an answer, not a fill
# --------------------------------------------------------------------------- #
def test_repeating_a_signal_creates_no_second_order(db_session) -> None:
    account, signal, _asset = _seed(db_session, tag="repeat")

    first = execute_signal(db_session, account, signal, settings=SETTINGS)
    after_first = _ledger(db_session, account.id)
    second = execute_signal(db_session, account, signal, settings=SETTINGS)
    after_second = _ledger(db_session, account.id)

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["order_id"] == first["order_id"]
    # Nothing moved the second time: not the cash, not the position, not the ledger, and
    # not a second "an order was executed" event.
    assert after_second == after_first
    assert after_first["orders"] == 1
    assert after_first["trades"] == 1
    assert after_first["audit_events"] == 1


def test_repeating_a_signal_many_times_keeps_one_order(db_session) -> None:
    account, signal, _asset = _seed(db_session, tag="many")

    results = [execute_signal(db_session, account, signal, settings=SETTINGS) for _ in range(5)]

    assert [row["order_id"] for row in results] == [results[0]["order_id"]] * 5
    assert [row["idempotent_replay"] for row in results] == [False] + [True] * 4
    assert _ledger(db_session, account.id)["orders"] == 1
    assert len(_orders_for(db_session, signal.id)) == 1


def test_a_replay_reports_the_order_the_first_call_created(db_session) -> None:
    account, signal, _asset = _seed(db_session, tag="numbers")

    first = execute_signal(db_session, account, signal, settings=SETTINGS)
    replay = execute_signal(db_session, account, signal, settings=SETTINGS)

    for key in ("side", "order_id", "account_id"):
        assert replay[key] == first[key], key
    # "The order the first call created" means the stored row, so that is what the replay is
    # compared with: a money column has a scale of its own (fees is Numeric(20, 8)), while
    # the first answer was built from the numbers before that rounding.
    (order,) = _orders_for(db_session, signal.id)
    assert replay["quantity"] == order.quantity
    assert replay["fill_price"] == order.fill_price
    assert replay["fees"] == order.fees
    assert replay["slippage"] == order.slippage
    # Same fill as the first answer, up to the column scale, and the same balance: both
    # describe the execution that already happened rather than a new one.
    assert float(replay["fees"]) == pytest.approx(float(first["fees"]), rel=1e-9)
    assert float(replay["quantity"]) == pytest.approx(float(first["quantity"]), rel=1e-9)
    assert replay["realized_pnl"] == first["realized_pnl"]
    assert replay["cash"] == first["cash"]


def test_a_replayed_signal_ignores_a_different_size(db_session) -> None:
    """The first execution decided the size; a repeat cannot quietly trade a second one."""

    account, signal, _asset = _seed(db_session, tag="size")
    first = execute_signal(db_session, account, signal, settings=SETTINGS)
    before = _ledger(db_session, account.id)

    replay = execute_signal(db_session, account, signal, settings=SETTINGS, quantity=Decimal("1"))

    assert replay["idempotent_replay"] is True
    assert replay["quantity"] == first["quantity"]
    assert _ledger(db_session, account.id) == before


def test_a_different_signal_is_a_different_order(db_session) -> None:
    account, buy, asset = _seed(db_session, tag="different")
    sell = _next_signal(db_session, buy, asset, state="SELL", days=3)

    bought = execute_signal(db_session, account, buy, settings=SETTINGS)
    sold = execute_signal(db_session, account, sell, settings=SETTINGS, quantity=Decimal("4"))

    assert bought["order_id"] != sold["order_id"]
    assert len(_orders_for(db_session, buy.id)) == 1
    assert len(_orders_for(db_session, sell.id)) == 1
    assert _ledger(db_session, account.id)["orders"] == 2


# --------------------------------------------------------------------------- #
# The ledger does not move twice
# --------------------------------------------------------------------------- #
def test_a_repeated_partial_close_does_not_sell_the_position_twice(db_session) -> None:
    account, buy, asset = _seed(db_session, tag="partial")
    execute_signal(db_session, account, buy, settings=SETTINGS)
    sell = _next_signal(db_session, buy, asset, state="SELL", days=3)

    first = execute_signal(db_session, account, sell, settings=SETTINGS, quantity=Decimal("4"))
    before = _ledger(db_session, account.id)
    trades_before = db_session.query(PaperTrade).filter(PaperTrade.account_id == account.id).count()

    replay = execute_signal(db_session, account, sell, settings=SETTINGS, quantity=Decimal("4"))

    assert replay["idempotent_replay"] is True
    assert replay["order_id"] == first["order_id"]
    # Exactly what a repeated partial close used to get wrong: the position, the realised
    # P&L, the cash, the open slice and the trade history are all untouched, and the order
    # is still the filled one the first call wrote.
    assert _ledger(db_session, account.id) == before
    assert before["position"] > 0
    assert before["realized_pnl"] != 0
    assert (
        db_session.query(PaperTrade).filter(PaperTrade.account_id == account.id).count()
        == trades_before
    )
    (order,) = _orders_for(db_session, sell.id)
    assert order.status == "filled"
    assert order.quantity == first["quantity"]


# --------------------------------------------------------------------------- #
# The database is the last line (ADR-221)
# --------------------------------------------------------------------------- #
def test_the_database_refuses_a_second_order_for_one_signal(db_session) -> None:
    account, signal, asset = _seed(db_session, tag="constraint")
    execute_signal(db_session, account, signal, settings=SETTINGS)

    db_session.add(
        PaperOrder(
            account_id=account.id,
            signal_id=signal.id,
            asset_id=asset.id,
            side="BUY",
            quantity=Decimal("1"),
            status="filled",
            reason="signal",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    assert len(_orders_for(db_session, signal.id)) == 1


def test_orders_without_a_signal_do_not_collide(db_session) -> None:
    """Manual orders carry no signal, and any number of them may exist (SQL and PostgreSQL
    both treat NULLs as distinct in a unique index)."""

    account, _signal, asset = _seed(db_session, tag="nul")
    for _ in range(3):
        db_session.add(
            PaperOrder(
                account_id=account.id,
                signal_id=None,
                asset_id=asset.id,
                side="BUY",
                quantity=Decimal("1"),
                status="filled",
            )
        )
    db_session.commit()

    assert db_session.query(PaperOrder).filter(PaperOrder.signal_id.is_(None)).count() == 3


def test_a_lost_race_is_answered_with_the_order_the_winner_created(db_session, monkeypatch) -> None:
    """The check that cannot work, blinded on purpose.

    Both callers read "no order for this signal yet" and only then write — that is the
    race, and it is replayed here with two sessions and the second caller's pre-flight
    check blinded. The signal being raced is a partial close, the case the audit worried
    about: it moves the position, the cash and the ledger. What refuses the second
    application is the unique index, and what the caller gets back is the winner's order
    with the position exactly as the winner left it.
    """

    account, buy, asset = _seed(db_session, tag="race")
    execute_signal(db_session, account, buy, settings=SETTINGS)
    sell = _next_signal(db_session, buy, asset, state="SELL", days=3)

    real = paper_engine._existing_order
    seen = {"calls": 0}

    def _blind_to_the_winner(db, signal_id):  # noqa: ANN001
        seen["calls"] += 1
        if seen["calls"] == 2:
            return None
        return real(db, signal_id)

    monkeypatch.setattr(paper_engine, "_existing_order", _blind_to_the_winner)

    winner = execute_signal(db_session, account, sell, settings=SETTINGS, quantity=Decimal("4"))
    before = _ledger(db_session, account.id)

    with Session(db_session.get_bind()) as loser:
        account_row = loser.get(PaperAccount, account.id)
        signal_row = loser.get(Signal, sell.id)
        answer = execute_signal(
            loser, account_row, signal_row, settings=SETTINGS, quantity=Decimal("4")
        )

    # Calls: the winner's pre-flight, the loser's blinded pre-flight, the loser's recovery
    # lookup after its insert was refused.
    assert seen["calls"] == 3
    assert answer["idempotent_replay"] is True
    assert answer["order_id"] == winner["order_id"]
    assert answer["quantity"] == winner["quantity"]

    # The loser's partial close was rolled back whole: the position, the realised P&L, the
    # cash, the ledger and the trade history are exactly what the winner left behind.
    assert _ledger(db_session, account.id) == before
    assert before["orders"] == 2  # the buy and the sell
    assert before["audit_events"] == 2
    assert answer["cash"] == winner["cash"]


def test_concurrent_executions_of_one_signal_produce_one_order(shared_engine) -> None:
    """Four threads, one signal: the database ends up with exactly one order."""

    with Session(shared_engine) as seed_db:
        account, signal, _asset = _seed(seed_db, tag="threads")
        account_id, signal_id = account.id, signal.id

    start = threading.Barrier(4)
    results: list[dict[str, Any]] = []
    failures: list[BaseException] = []

    def _execute() -> None:
        with Session(shared_engine) as db:
            account_row = db.get(PaperAccount, account_id)
            signal_row = db.get(Signal, signal_id)
            start.wait()
            try:
                results.append(execute_signal(db, account_row, signal_row, settings=SETTINGS))
            except BaseException as exc:  # noqa: BLE001 - asserted on below
                failures.append(exc)

    threads = [threading.Thread(target=_execute) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not [thread for thread in threads if thread.is_alive()]
    for failure in failures:
        # SQLite allows a single writer, so a loser can be told to come back instead of
        # being answered. That is the dialect's limit, not the rule's: PostgreSQL — the
        # production dialect and the CI job — answers every caller, so nothing may fail
        # there.
        assert isinstance(failure, OperationalError), failure
        assert shared_engine.dialect.name == "sqlite", failure
    assert results, failures

    # Exactly one execution did the fill; every other answer names that same order.
    assert sum(1 for row in results if row["idempotent_replay"] is False) == 1
    assert len({row["order_id"] for row in results}) == 1

    with Session(shared_engine) as db:
        ledger = _ledger(db, account_id)
    assert ledger["orders"] == 1
    assert ledger["trades"] == 1
    assert ledger["audit_events"] == 1
    assert ledger["position"] > 0
    # Cash moved once: every answer, winner and replay alike, reports the stored balance.
    assert {row["cash"] for row in results} == {ledger["cash"]}


def test_a_failed_execution_leaves_nothing_behind_and_can_be_retried(
    db_session, monkeypatch
) -> None:
    """A run that dies after the fill was written must not leave half an order behind."""

    account, signal, _asset = _seed(db_session, tag="retry")
    real = paper_engine.record_audit
    attempts = {"n": 0}

    def _fail_once(*args, **kwargs):  # noqa: ANN002, ANN003
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("the audit write failed")
        return real(*args, **kwargs)

    monkeypatch.setattr(paper_engine, "record_audit", _fail_once)

    with pytest.raises(RuntimeError):
        execute_signal(db_session, account, signal, settings=SETTINGS)
    db_session.rollback()

    failed = _ledger(db_session, account.id)
    assert failed["orders"] == 0
    assert failed["trades"] == 0
    assert failed["position"] is None
    assert failed["cash"] == Decimal("10000")
    assert failed["audit_events"] == 0

    result = execute_signal(db_session, account, signal, settings=SETTINGS)

    assert result["idempotent_replay"] is False
    retried = _ledger(db_session, account.id)
    assert retried["orders"] == 1
    assert retried["trades"] == 1
    assert retried["audit_events"] == 1
    assert retried["cash"] == result["cash"]


# --------------------------------------------------------------------------- #
# The API says which answer it is giving
# --------------------------------------------------------------------------- #
def test_the_api_says_when_it_answered_with_an_earlier_order(client, db_session) -> None:
    account, signal, _asset = _seed(db_session, tag="api")

    first = client.post(
        f"/api/v1/paper/accounts/{account.id}/execute",
        json={"signal_id": signal.id, "fee_bps": 0, "slippage_bps": 0},
    )
    second = client.post(
        f"/api/v1/paper/accounts/{account.id}/execute",
        json={"signal_id": signal.id, "fee_bps": 0, "slippage_bps": 0},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    for key in ("order_id", "quantity", "fill_price", "fees", "slippage", "realized_pnl", "cash"):
        assert second.json()[key] == first.json()[key], key
    assert db_session.query(PaperOrder).filter(PaperOrder.signal_id == signal.id).count() == 1
