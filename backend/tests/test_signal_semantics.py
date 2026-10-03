"""What a signal actually means (ADR-115, docs/09 §1/§4/§7).

Three promises are pinned here:

1. **An exit is an event, an entry is a level.** ``SELL`` fires on the bar the
   exit rule first holds, not on every bar it keeps holding; a ``BUY`` still
   describes a state the rules satisfy.
2. **A closing instruction says what it closes.** ``direction`` alone could not
   distinguish "sell my long" from "open a short", so exits carry
   ``closes_direction`` and the outcome evaluator only scores real entries.
3. **Nothing stale is stored.** A series whose latest closed bar holds no fresh
   event writes no row, and a manual scan cannot dump ``NO_SIGNAL`` noise or
   invent a ``created`` count.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pandas as pd
import pytest

from app.domain.models import (
    Asset,
    FeatureSnapshot,
    MarketDataSeries,
    MarketDataSource,
    Signal,
    SignalOutcome,
    Strategy,
    StrategyVersion,
)
from app.notifications.config import update_notification_config
from app.notifications.service import notify_pending_signals
from app.simulation.outcome_evaluator import evaluate_pending_outcomes
from app.simulation.signal_engine import scan_series
from app.strategies.dsl import StrategySpec
from app.strategies.executor import run_strategy

WEBHOOK_URL = "https://hooks.example.com/quantlab-semantics"


def _frame(closes: list[float], ema20: list[float], ema50: list[float] | None = None):
    index = pd.date_range("2024-01-01", periods=len(closes), freq="D", tz="UTC")
    data = {"close": closes, "ema20": ema20}
    if ema50 is not None:
        data["ema50"] = ema50
    return pd.DataFrame(data, index=index)


def _spec(entry_long: dict, exit_long: dict, **market) -> StrategySpec:
    return StrategySpec.model_validate(
        {
            "schema_version": "1.0",
            "strategy": {"id": "sem", "name": "SEM", "version": "1.0.0"},
            "market": {"asset_classes": ["stock"], "timeframes": ["1d"], **market},
            "entry": {"long": entry_long},
            "exit": {"long": exit_long},
            "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
        }
    )


_BELOW = {"all": [{"op": "lt", "left": "close", "right": "ema20"}]}
_ABOVE = {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}


# --------------------------------------------------------------------------- #
# Intent semantics
# --------------------------------------------------------------------------- #
def test_a_holding_exit_level_is_not_an_event() -> None:
    spec = _spec(_BELOW, _ABOVE)
    # close has been above ema20 for the whole window: the exit "holds", but it
    # did not start holding here, so there is nothing new to act on.
    _out, intent = run_strategy(spec, _frame([12.0, 11.0, 10.5], [10.0, 10.0, 10.0]))
    assert intent["state"] == "NO_SIGNAL"
    assert intent["closes_direction"] is None


def test_the_bar_an_exit_first_holds_is_a_sell_that_names_what_it_closes() -> None:
    spec = _spec(_BELOW, _ABOVE)
    frame = _frame([9.0, 10.0, 12.0], [10.0, 10.0, 10.0])
    _out, intent = run_strategy(spec, frame)
    assert intent["state"] == "SELL"
    assert intent["direction"] == "FLAT"
    assert intent["closes_direction"] == "LONG"
    assert pd.Timestamp(intent["bar_time"]) == frame.index[-1]


def test_a_closing_short_names_the_short_direction() -> None:
    spec = StrategySpec.model_validate(
        {
            "schema_version": "1.0",
            "strategy": {"id": "sem-short", "name": "SEM", "version": "1.0.0"},
            "market": {"asset_classes": ["stock"], "timeframes": ["1d"], "allow_short": True},
            "entry": {"short": _ABOVE},
            "exit": {"short": _BELOW},
            "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
        }
    )
    _out, intent = run_strategy(spec, _frame([11.0, 12.0, 8.0], [10.0, 10.0, 10.0]))
    assert intent["state"] == "SELL"
    assert intent["closes_direction"] == "SHORT"


def test_an_exit_outranks_an_entry_that_still_holds() -> None:
    # close > ema50 keeps holding (a level entry), while close > ema20 starts
    # holding on the last bar (an exit event). The exit wins: the entry is still
    # there next bar, the event will not be.
    spec = _spec({"all": [{"op": "gt", "left": "close", "right": "ema50"}]}, _ABOVE)
    _out, intent = run_strategy(spec, _frame([10.0, 13.0], [12.0, 12.0], ema50=[9.0, 9.0]))
    assert intent["state"] == "SELL"
    assert intent["closes_direction"] == "LONG"


def test_an_entry_is_still_a_level() -> None:
    # The asymmetry is deliberate (ADR-115): BUY means "the entry rules are
    # satisfied on the latest closed bar", so it keeps reporting while it holds.
    spec = _spec(_BELOW, _ABOVE)
    _out, intent = run_strategy(spec, _frame([9.0, 9.5], [10.0, 10.0]))
    assert intent["state"] == "BUY"
    assert intent["direction"] == "LONG"
    assert intent["closes_direction"] is None


def test_a_missing_bar_is_not_a_true_flag() -> None:
    # NaN means "no bar", not "the rule holds" — otherwise a gap could look like
    # an exit that just fired.
    from app.strategies.executor import _is_true

    assert _is_true(True) is True
    assert _is_true(0) is False
    assert _is_true(None) is False
    assert _is_true(float("nan")) is False


# --------------------------------------------------------------------------- #
# Persistence & scanning
# --------------------------------------------------------------------------- #
def _seed_series(db, dsl: dict, bars, *, symbol: str, slug: str):
    from app.data.market_data_repo import frame_to_bars, upsert_bars

    strategy = Strategy(name=f"S-{symbol}", slug=slug)
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json=dsl,
        immutable_hash="s" * 64,
        validation_status="valid",
        is_current=True,
    )
    db.add(version)
    db.flush()
    asset = Asset(symbol=symbol, asset_class="stock")
    db.add(asset)
    db.flush()
    source = MarketDataSource(name=f"src-{slug}", base_url="x")
    db.add(source)
    db.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db.add(series)
    db.flush()
    upsert_bars(db, series, frame_to_bars(bars))
    db.commit()
    return version, series


def _dsl(entry_long: dict, exit_long: dict) -> dict:
    return {
        "schema_version": "1.0",
        "strategy": {"id": "sem-db", "name": "SEM-DB", "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "entry": {"long": entry_long},
        "exit": {"long": exit_long},
        "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
    }


def test_a_series_with_nothing_fresh_writes_nothing(db_session, sample_bars) -> None:
    # sample_bars closes its last bar below ema20: the exit has been holding for
    # many bars (no event) and the entry does not hold (no level). Nothing here
    # is a signal, so nothing may be stored.
    version, series = _seed_series(
        db_session, _dsl(_ABOVE, _BELOW), sample_bars, symbol="STALE", slug="sem-stale"
    )

    assert scan_series(db_session, version, series) is None
    assert db_session.query(Signal).count() == 0
    assert db_session.query(FeatureSnapshot).count() == 0


def test_manual_scan_persists_only_fresh_events_and_counts_honestly(
    client, db_session, sample_bars
) -> None:
    _seed_series(db_session, _dsl(_BELOW, _ABOVE), sample_bars, symbol="FRESH", slug="sem-fresh")

    # Dry run: it reports what it sees and writes nothing.
    dry = client.post("/api/v1/signals/scan").json()
    assert dry["created"] == 0
    assert db_session.query(Signal).count() == 0
    assert [row["state"] for row in dry["signals"]] == ["BUY"]

    first = client.post("/api/v1/signals/scan?persist=true")
    assert first.status_code == 200
    body = first.json()
    assert body["created"] == 1
    assert db_session.query(Signal).count() == 1
    # The persisted row is the entry, and it says so.
    stored = db_session.query(Signal).one()
    assert stored.state == "BUY"
    assert stored.closes_direction is None

    # Re-running must not double-insert (this used to raise IntegrityError → 500).
    second = client.post("/api/v1/signals/scan?persist=true")
    assert second.status_code == 200
    assert second.json()["created"] == 0
    assert db_session.query(Signal).count() == 1


def test_the_evaluator_scores_entries_and_ignores_closes(db_session, sample_bars) -> None:
    version, series = _seed_series(
        db_session, _dsl(_BELOW, _ABOVE), sample_bars, symbol="EVAL", slug="sem-eval"
    )
    signal_bar = sample_bars.index[100]
    entry = Signal(
        strategy_version_id=version.id,
        asset_id=series.asset_id,
        timeframe="1d",
        bar_timestamp=signal_bar.to_pydatetime(),
        state="BUY",
        direction="LONG",
        closes_direction=None,
        triggered_rules_json=["ema_cross"],
        feature_snapshot_hash="f" * 64,
        data_source="series:1/v1",
    )
    close = Signal(
        strategy_version_id=version.id,
        asset_id=series.asset_id,
        timeframe="1d",
        bar_timestamp=sample_bars.index[120].to_pydatetime(),
        state="SELL",
        direction="FLAT",
        closes_direction="LONG",
        triggered_rules_json=["ema_cross"],
        feature_snapshot_hash="f" * 64,
        data_source="series:1/v1",
    )
    db_session.add_all([entry, close])
    db_session.commit()

    result = evaluate_pending_outcomes(db_session)
    # Only the entry is work; the closing instruction is counted, not scored.
    assert result["evaluated"] == 1
    assert result["not_an_entry"] == 1
    outcomes = db_session.query(SignalOutcome).all()
    assert [outcome.signal_id for outcome in outcomes] == [entry.id]


# --------------------------------------------------------------------------- #
# Notification noise control
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _seed_signal(db_session, *, status: str) -> Signal:
    strategy = Strategy(name=f"S-{status}", slug=f"sem-notify-{status}")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="n" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol=f"N-{status[:4].upper()}", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name=f"src-n-{status}", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state="BUY",
        direction="LONG",
        price_reference=100.0,
        triggered_rules_json=["ema_cross"],
        feature_snapshot_hash="f" * 64,
        data_source=f"series:{series.id}/v1",
        status=status,
    )
    db_session.add(signal)
    db_session.commit()
    db_session.refresh(signal)
    return signal


@pytest.fixture()
def _webhook(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _post(url, content=None, headers=None, timeout=None, follow_redirects=None, **kwargs):
        calls.append({"url": url})
        return _Resp(200)

    monkeypatch.setattr(httpx, "post", _post)
    return calls


def test_an_acknowledged_signal_is_not_pushed(db_session, _webhook) -> None:
    update_notification_config(db_session, {"enabled": True, "webhook_url": WEBHOOK_URL})
    signal = _seed_signal(db_session, status="acknowledged")

    result = notify_pending_signals(db_session)
    assert result["candidates"] == 0
    assert result["sent"] == 0
    assert _webhook == []
    db_session.refresh(signal)
    assert signal.notified_at is None

    # It is the acknowledgement that held it back: un-acknowledged, it goes out.
    signal.status = "new"
    db_session.commit()
    assert notify_pending_signals(db_session)["sent"] == 1
    assert len(_webhook) == 1
