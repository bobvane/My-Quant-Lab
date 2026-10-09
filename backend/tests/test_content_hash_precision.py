"""A content hash must change exactly when the content changes (ADR-198).

Three copies of one hash formatted their input with ``float_format="%.10g"`` — ten
significant digits. ``MarketDataBar`` stores prices and volumes as ``Numeric(20,
8)``, so a price such as ``60000.12345678`` carries thirteen significant digits: it
hashed the same as ``60000.12345679``, and ``1000000.0`` hashed the same as
``1000000.0000001``. Two different datasets therefore carried one
``dataset_hash``, which is precisely what "reproducible backtests" may not do.

The three copies also disagreed with each other: ``series_content_hash`` hashed
whatever columns the frame happened to carry while ``feature_input_hash`` hashed
only OHLCV, so a frame with one extra column produced a different value on each
path — the run row's ``dataset_hash`` and the digest inside its own
``result_hash`` described the same bars with two different numbers.

These tests pin both halves of the fix: every stored digit survives into the
digest, and there is one implementation rather than three.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import app
from app.data.market_data_repo import series_content_hash
from app.features.engine import feature_input_hash, frame_content_hash
from app.simulation.signal_engine import _feature_hash

#: A price the database really stores: ``Numeric(20, 8)`` keeps eight decimals, so a
#: five-digit price carries thirteen significant digits — three past ``"%.10g"``.
_BASE: dict[str, float] = {
    "open": 60000.12345678,
    "high": 60100.5,
    "low": 59900.25,
    "close": 60050.75,
    "volume": 1_000_000.0,
}

_OHLCV = ("open", "high", "low", "close", "volume")


def _frame(**overrides: float) -> pd.DataFrame:
    row: dict[str, float] = dict(_BASE)
    row.update(overrides)
    index = pd.DatetimeIndex([pd.Timestamp("2025-01-02", tz="UTC")], name="timestamp")
    return pd.DataFrame([row], index=index)


#: Pairs whose only difference sits below the tenth significant digit of one column.
_COLLIDING_PAIRS: list[tuple[dict[str, float], dict[str, float]]] = [
    ({"close": 60000.12345678}, {"close": 60000.12345679}),
    ({"high": 1000000.0}, {"high": 1000000.0000001}),
    ({"low": 0.123456789012}, {"low": 0.123456789013}),
]


@pytest.mark.parametrize(
    ("left", "right"),
    _COLLIDING_PAIRS,
    ids=["price-beyond-eight-decimals", "trailing-zeros-of-a-big-number", "fraction"],
)
def test_two_datasets_that_differ_beyond_ten_digits_do_not_share_a_hash(
    left: dict[str, float], right: dict[str, float]
) -> None:
    first, second = _frame(**left), _frame(**right)

    assert not first.equals(second), "the pair has to be two different frames to prove anything"
    assert frame_content_hash(first) != frame_content_hash(second)
    assert feature_input_hash(first) != feature_input_hash(second)
    assert series_content_hash(first) != series_content_hash(second)


def test_the_series_hash_and_the_run_hash_are_the_same_statement() -> None:
    frame = _frame()
    extra = frame.copy()
    extra["symbol"] = "DEMO-AAPL"

    assert series_content_hash(frame) == feature_input_hash(frame)
    assert series_content_hash(extra) == feature_input_hash(extra), (
        "the dataset_hash on a run row and the one inside its result_hash must not "
        "disagree because a caller added a column"
    )


def test_the_hash_does_not_depend_on_the_column_order() -> None:
    frame = _frame()
    shuffled = frame[sorted(frame.columns, reverse=True)]

    assert frame_content_hash(frame) == frame_content_hash(shuffled)
    assert frame_content_hash(frame) == frame_content_hash(shuffled[list(_OHLCV)])


def test_a_missing_column_is_an_error_not_a_silent_pass() -> None:
    frame = _frame().drop(columns=["volume"])

    with pytest.raises(KeyError):
        feature_input_hash(frame)


def test_the_signal_reader_uses_the_same_implementation() -> None:
    frame = _frame()

    assert _feature_hash(frame) == frame_content_hash(frame)


def test_a_frame_with_no_rows_still_hashes() -> None:
    empty = pd.DataFrame(columns=list(_OHLCV))

    assert series_content_hash(empty) == feature_input_hash(empty)


def test_no_application_module_hashes_truncated_csv_text() -> None:
    """The defect is a formatting choice; a source scan catches every new copy of it.

    Only the *hash* pattern is forbidden. ``app/compiler/mapping.py:139`` writes a
    numeric operand into DSL text with ``"%.10g"`` on purpose — docs/29 §10.4 freezes
    that canonical form for spec text, which is a different statement from a digest
    of stored bars.
    """

    root = Path(app.__file__).parent
    offenders = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "to_csv(float_format=" in path.read_text(encoding="utf-8")
    )

    assert offenders == [], f"a content hash is truncating its input again: {offenders}"
