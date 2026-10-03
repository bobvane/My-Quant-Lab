"""Signal outcome evaluation: track what happened after each signal fired.

When a signal is generated, the outcome is unknown. A periodic Celery task
looks forward in the price data and records whether the signal would have been
profitable, along with MAE/MFE over the evaluation window.

This is the mechanism that lets the user answer: "过去生成的信号后来到底
怎么样了？" — the "learn from results" promise in the product spec.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import MarketDataSeries, Signal, SignalOutcome

logger = logging.getLogger(__name__)

__all__ = ["evaluate_pending_outcomes"]

DEFAULT_BARS_AFTER = 10


def evaluate_pending_outcomes(
    db: Session, *, bars_after: int = DEFAULT_BARS_AFTER
) -> dict[str, int]:
    """Evaluate signals that don't have an outcome yet.

    For each pending signal, look forward ``bars_after`` bars from the signal
    bar and record:

    * ``pnl_pct``: close-after-N-bars vs close-at-signal (direction-adjusted)
    * ``mae``: worst adverse excursion from the signal close
    * ``mfe``: best favourable excursion from the signal close

    Only *entries* are evaluated: an exit is not a position, so asking what the
    price did after it is a different question with a different sign. The query
    excludes them instead of skipping them in the loop, so a table full of exits
    cannot fill the pending window and stall the entries behind it (ADR-115).

    Returns counts: {"evaluated": N, "insufficient_data": M, "skipped": K,
    "not_an_entry": E} — ``E`` counts the closing signals, which are not work.
    """

    from app.data.market_data_repo import load_bars

    # Find entry signals without an outcome row.
    pending = db.scalars(
        select(Signal)
        .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .where(
            SignalOutcome.id.is_(None),
            Signal.closes_direction.is_(None),
            Signal.direction.in_(("LONG", "SHORT")),
        )
        .order_by(Signal.id)
        .limit(200)
    ).all()

    not_an_entry = db.scalar(
        select(func.count())
        .select_from(Signal)
        .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .where(SignalOutcome.id.is_(None), Signal.closes_direction.is_not(None))
    )

    if not pending:
        return {
            "evaluated": 0,
            "insufficient_data": 0,
            "skipped": 0,
            "not_an_entry": int(not_an_entry or 0),
        }

    # Cache series → bars to avoid re-loading for multiple signals on the same series.
    series_cache: dict[int, list] = {}

    evaluated = 0
    insufficient = 0
    skipped = 0

    for signal in pending:
        signal_ts = signal.bar_timestamp
        if signal_ts.tzinfo is None:
            signal_ts = signal_ts.replace(tzinfo=dt.UTC)

        series = db.scalar(
            select(MarketDataSeries).where(
                MarketDataSeries.asset_id == signal.asset_id,
                MarketDataSeries.timeframe == signal.timeframe,
            )
        )
        if series is None:
            skipped += 1
            continue

        if series.id not in series_cache:
            series_cache[series.id] = load_bars(db, series, only_closed=True, limit=2000)
        bars = series_cache[series.id]

        signal_close = _close_at_or_before(bars, signal_ts)
        if signal_close is None:
            skipped += 1
            continue

        # Bars strictly AFTER the signal bar.
        future = bars[bars.index > signal_ts].head(bars_after)
        if len(future) < bars_after:
            insufficient += 1
            continue

        direction_sign = 1.0 if signal.direction == "LONG" else -1.0
        close_end = float(future["close"].iloc[-1])
        pnl_pct = (close_end - signal_close) / signal_close * direction_sign

        highest = float(future["high"].max())
        lowest = float(future["low"].min())
        if direction_sign > 0:
            mae = max(0.0, signal_close - lowest) / signal_close
            mfe = max(0.0, highest - signal_close) / signal_close
        else:
            mae = max(0.0, highest - signal_close) / signal_close
            mfe = max(0.0, signal_close - lowest) / signal_close

        outcome_state = "profitable" if pnl_pct > 0 else "unprofitable"
        outcome = SignalOutcome(
            signal_id=signal.id,
            outcome_state=outcome_state,
            entry_time=signal_ts,
            exit_time=future.index[-1],
            entry_price=signal_close,
            exit_price=close_end,
            pnl_pct=round(pnl_pct, 8),
            mae=round(mae, 8),
            mfe=round(mfe, 8),
            evaluated_at=dt.datetime.now(tz=dt.UTC),
            notes=f"evaluated over {bars_after} bars after signal",
        )
        db.add(outcome)
        evaluated += 1

    db.commit()
    return {
        "evaluated": evaluated,
        "insufficient_data": insufficient,
        "skipped": skipped,
        "not_an_entry": int(not_an_entry or 0),
    }


def _close_at_or_before(bars, timestamp) -> float | None:
    """Close price of the bar at or immediately before ``timestamp``."""

    if bars.empty:
        return None
    mask = bars.index <= timestamp
    if not mask.any():
        return None
    return float(bars.loc[mask, "close"].iloc[-1])
