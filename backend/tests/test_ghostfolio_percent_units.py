"""Ghostfolio writes percentages in two different units; the payload decides which.

Ghostfolio has shipped ``allocationInPercentage`` as a 0–1 fraction (0.25 = 25 %)
and as percent points (25 = 25 %) in different versions, and the adapter used to
answer that question **per value** — ``value * 100 if value <= 1 else value``.
That rule is wrong exactly where it matters most: in a percent-point payload a
holding weighted 0.5 % was reported as 50 %, and one weighted 1 % as 100 %. A
hundred-fold error in a number the user reads as their position size, and one
that then travels into the signal's portfolio context note.

The unit cannot be decided from a single number (``0.5`` is a legitimate 0.5 %
in one convention and 50 % in the other), so it is decided **once for the whole
payload** from the money amounts the payload carries next to the percentages:
``value / total * 100`` and ``unrealized_pnl / investment * 100`` are the same
quantity computed independently, and the reading that matches them wins.
"""

from __future__ import annotations

from app.data.ghostfolio import GhostfolioAdapter, _as_pct, _percent_scale

# Construction only stores config; these tests never touch the network.
ADAPTER = GhostfolioAdapter(base_url="http://ghostfolio.local", token="x" * 32)


def _holding(
    symbol: str,
    *,
    value: float | None = None,
    allocation: float | None = None,
    investment: float | None = None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
) -> dict:
    item: dict = {"assetProfile": {"symbol": symbol}, "quantity": 10, "currency": "USD"}
    if value is not None:
        item["valueInBaseCurrency"] = value
    if allocation is not None:
        item["allocationInPercentage"] = allocation
    if investment is not None:
        item["investment"] = investment
    if pnl is not None:
        item["netPerformance"] = pnl
    if pnl_pct is not None:
        item["netPerformancePercent"] = pnl_pct
    return item


def test_a_payload_written_in_percent_points_is_read_as_percent_points() -> None:
    """99.5 / 1 / 0.5 are weights, not fractions: 0.5 means half a percent."""

    payload = {
        "holdings": [
            _holding(
                "AAPL", value=98500, allocation=98.5, investment=80000, pnl=18500, pnl_pct=23.125
            ),
            _holding("MSFT", value=1000, allocation=1, investment=1000, pnl=0, pnl_pct=0),
            _holding("DUST", value=500, allocation=0.5, investment=10000, pnl=50, pnl_pct=0.5),
        ]
    }

    holdings = ADAPTER._parse_holdings_payload(payload)

    assert [h["allocation_pct"] for h in holdings] == [98.5, 1.0, 0.5]
    assert [h["unrealized_pnl_pct"] for h in holdings] == [23.125, 0.0, 0.5]


def test_a_payload_written_as_fractions_is_still_read_as_fractions() -> None:
    payload = {
        "holdings": [
            _holding(
                "AAPL", value=25000, allocation=0.25, investment=20000, pnl=5000, pnl_pct=0.25
            ),
            _holding(
                "MSFT", value=75000, allocation=0.75, investment=60000, pnl=15000, pnl_pct=0.25
            ),
        ]
    }

    holdings = ADAPTER._parse_holdings_payload(payload)

    assert [h["allocation_pct"] for h in holdings] == [25.0, 75.0]
    assert [h["unrealized_pnl_pct"] for h in holdings] == [25.0, 25.0]


def test_a_single_holding_written_as_a_fraction_is_read_as_one() -> None:
    """1.0 in a one-asset payload is 100 %, not 1 %."""

    holdings = ADAPTER._parse_holdings_payload(
        {"holdings": [_holding("ONLY", value=1000, allocation=1.0)]}
    )

    assert holdings[0]["allocation_pct"] == 100.0


def test_the_net_performance_is_decided_from_its_own_evidence() -> None:
    """No allocation at all: the pnl percentage still gets read correctly."""

    holdings = ADAPTER._parse_holdings_payload(
        {"holdings": [_holding("X", value=1000, investment=10000, pnl=50, pnl_pct=0.5)]}
    )

    assert holdings[0]["allocation_pct"] is None
    assert holdings[0]["unrealized_pnl_pct"] == 0.5


def test_a_payload_without_money_amounts_keeps_the_legacy_reading() -> None:
    """Nothing to check 0.25 against; the payload is a fraction set (they sum to 1)."""

    holdings = ADAPTER._parse_holdings_payload(
        {"holdings": [_holding("A", allocation=0.6), _holding("B", allocation=0.4)]}
    )

    assert [h["allocation_pct"] for h in holdings] == [60.0, 40.0]


def test_a_payload_of_only_large_weights_is_read_as_percent_points() -> None:
    holdings = ADAPTER._parse_holdings_payload(
        {"holdings": [_holding("A", allocation=3.4), _holding("B", allocation=1.2)]}
    )

    assert [h["allocation_pct"] for h in holdings] == [3.4, 1.2]


def test_a_fragment_the_payload_cannot_settle_keeps_the_legacy_reading() -> None:
    """One asset whose value is the whole total, reported as 0.5.

    The payload contradicts itself (a one-asset portfolio is 100 % by definition),
    so no reading is provable. The legacy per-value reading stands, and this test
    exists so that choice stays visible instead of being made by accident.
    """

    holdings = ADAPTER._parse_holdings_payload(
        {"holdings": [_holding("ONLY", value=500, allocation=0.5)]}
    )

    assert holdings[0]["allocation_pct"] == 50.0


def test_the_scale_helper_says_when_it_has_no_evidence() -> None:
    assert _percent_scale([0.25], [None]) == 100.0
    assert _percent_scale([25.0], [None]) == 1.0
    # A lone 5.0 is evidence: a 0-1 fraction cannot exceed 1.
    assert _percent_scale([5.0], [None]) == 1.0
    # All values under 1 and summing to neither ≈1 nor ≈100: no evidence.
    assert _percent_scale([0.8, 0.9], [None, None]) is None
    assert _percent_scale([], []) is None


def test_the_legacy_reading_is_still_the_documented_fallback() -> None:
    assert _as_pct(0.5) == 50.0
    assert _as_pct(5.0) == 5.0
    assert _as_pct(0.5, 1.0) == 0.5
    assert _as_pct(0.5, 100.0) == 50.0
    assert _as_pct(None, 1.0) is None
