"""Bar lifecycle: a forming bar is refreshed when it closes, and closure is per timeframe.

Two audit findings meet here.

* **D1 — the sync only ever inserted.** A bar stored while it was still forming kept
  ``is_closed=False`` forever, and every closed-bar reader (backtests, signal scans,
  outcome evaluation) silently stopped at the day before the first sync. The horizon on
  screen still looked current, because ``series_end`` counts the forming bar too.
* **D2 — closure was decided by comparing dates.** Only the last bar was ever examined
  and kept closed unless its *date* was today, so a weekly bar whose timestamp is this
  Monday was reported closed on Wednesday, and an hourly bar that was still running was
  reported closed as well. Half-finished high/low/close then entered features and
  backtests as final values.

The tests below pin the behaviour both fixes have to produce: an existing timestamp is
updated in place when the provider reports it differently, and a bar counts as closed
only once its own period is over.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from app.api.schemas import MarketDataSyncRequest
from app.data.market_data_repo import (
    frame_to_bars,
    get_or_create_series,
    load_bars,
    upsert_bars,
)
from app.data.providers import mark_closed_bars
from app.domain.models import Asset, MarketDataSeries, MarketDataSource


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _frame(index: pd.DatetimeIndex, close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000.0,
        },
        index=index,
    ).rename_axis("timestamp")


def _series(db_session, symbol: str = "LIFECYCLE") -> MarketDataSeries:
    asset = Asset(symbol=symbol, asset_class="stock")
    source = MarketDataSource(name=f"src-{symbol}", base_url="x")
    db_session.add_all([asset, source])
    db_session.flush()
    return get_or_create_series(db_session, asset=asset, timeframe="1d", source_id=source.id)


def _closed_bars(db_session, series_id: int) -> pd.DataFrame:
    series = db_session.get(MarketDataSeries, series_id)
    assert series is not None
    return load_bars(db_session, series, only_closed=True)


# --------------------------------------------------------------------------- #
# D2 — closure is decided by the bar's own period
# --------------------------------------------------------------------------- #
def test_a_weekly_bar_for_the_current_week_is_not_closed() -> None:
    """The timestamp of a weekly bar is its Monday, so it is younger than "today"
    from Tuesday onwards — a date comparison calls the running week finished."""

    index = pd.date_range("2026-09-21", periods=2, freq="7D", tz="UTC")  # Mon 09-21, Mon 09-28
    out = mark_closed_bars(
        _frame(index), "1w", "UTC", now=dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)
    )

    assert list(out["is_closed"]) == [True, False]


def test_last_weeks_weekly_bar_is_closed_once_the_week_is_over() -> None:
    index = pd.date_range("2026-09-21", periods=2, freq="7D", tz="UTC")
    out = mark_closed_bars(
        _frame(index), "1w", "UTC", now=dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.UTC)
    )

    assert list(out["is_closed"]) == [True, True]


def test_the_hour_that_is_still_running_is_not_closed() -> None:
    index = pd.date_range("2026-09-30 14:00", periods=2, freq="1h", tz="UTC")
    out = mark_closed_bars(
        _frame(index), "1h", "UTC", now=dt.datetime(2026, 9, 30, 15, 10, tzinfo=dt.UTC)
    )

    assert list(out["is_closed"]) == [True, False]


def test_an_hourly_bar_is_closed_once_its_hour_has_passed() -> None:
    index = pd.date_range("2026-09-30 14:00", periods=2, freq="1h", tz="UTC")
    out = mark_closed_bars(
        _frame(index), "1h", "UTC", now=dt.datetime(2026, 9, 30, 16, 5, tzinfo=dt.UTC)
    )

    assert list(out["is_closed"]) == [True, True]


def test_a_daily_bar_that_is_not_the_last_one_is_still_judged_on_its_own_day() -> None:
    """A window that runs past midnight must not call today's session finished."""

    index = pd.date_range("2026-09-28", periods=4, freq="D", tz="UTC")  # …09-30, 10-01
    out = mark_closed_bars(
        _frame(index), "1d", "UTC", now=dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)
    )

    assert list(out["is_closed"]) == [True, True, False, False]


def test_a_timeframe_without_a_known_period_is_never_assumed_closed() -> None:
    """Guessing a period would be the same mistake one level up: the safe answer for
    "we do not know how long this bar lasts" is "not proven finished"."""

    index = pd.DatetimeIndex([pd.Timestamp("2026-09-28", tz="UTC")])
    out = mark_closed_bars(_frame(index), "3d", "UTC", now=dt.datetime(2026, 12, 31, tzinfo=dt.UTC))

    assert list(out["is_closed"]) == [False]


# --------------------------------------------------------------------------- #
# D1 — an existing timestamp is refreshed, not skipped
# --------------------------------------------------------------------------- #
def test_a_second_upsert_refreshes_a_bar_instead_of_ignoring_it(db_session) -> None:
    series = _series(db_session)
    stamps = pd.date_range("2026-09-28", periods=3, freq="D", tz="UTC")
    forming = frame_to_bars(_frame(stamps))
    forming[-1]["is_closed"] = False

    report: dict[str, int] = {}
    assert upsert_bars(db_session, series, forming, report=report) == 3
    assert report["updated"] == 0
    assert len(_closed_bars(db_session, series.id)) == 2

    # The same bar comes back finished, with the prices the provider settled on.
    final = frame_to_bars(_frame(stamps, close=100.0))
    final[-1] = {**final[-1], "close": 101.0, "is_closed": True}
    assert upsert_bars(db_session, series, final, report=report) == 0

    assert report["updated"] == 1
    closed = _closed_bars(db_session, series.id)
    assert len(closed) == 3
    assert float(closed["close"].iloc[-1]) == 101.0
    # still one row per timestamp
    assert len(load_bars(db_session, series, only_closed=False)) == 3


def test_resyncing_identical_bars_writes_nothing(db_session) -> None:
    series = _series(db_session, symbol="IDEMPOTENT")
    bars = frame_to_bars(_frame(pd.date_range("2026-09-28", periods=3, freq="D", tz="UTC")))

    report: dict[str, int] = {}
    assert upsert_bars(db_session, series, bars, report=report) == 3
    assert upsert_bars(db_session, series, bars, report=report) == 0
    assert report["updated"] == 0


def test_a_later_sync_moves_the_closed_horizon_forward(client, db_session, monkeypatch) -> None:
    """End to end through ``POST /market-data/sync``: the first sync stores today's
    still-forming bar, the second one (a day later) must flip that same row to closed
    and hand the closed-bar readers one more bar."""

    import app.api.routers.market_data as market_data_router

    moment = {"now": dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)}

    def _mark(frame, timeframe, timezone_name="UTC"):
        return mark_closed_bars(frame, timeframe, timezone_name, now=moment["now"])

    monkeypatch.setattr(market_data_router, "mark_closed_bars", _mark)

    payload = MarketDataSyncRequest(
        symbol="DEMO-AAPL",
        timeframe="1d",
        start=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
        end=dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC),
    ).model_dump(mode="json")

    first = client.post("/api/v1/market-data/sync", json=payload).json()
    assert first["inserted"] == 11
    assert first["still_forming_bars"] == 1
    assert len(_closed_bars(db_session, first["series_id"])) == 10

    moment["now"] = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC)
    second = client.post("/api/v1/market-data/sync", json=payload).json()

    assert second["inserted"] == 0, "the timestamps already exist"
    assert second["updated"] == 1, "the previously forming bar is now final"
    assert second["still_forming_bars"] == 0
    assert len(_closed_bars(db_session, second["series_id"])) == 11
