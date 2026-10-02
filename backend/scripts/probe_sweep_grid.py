"""Print the coalition-total grid a sweep would derive, for the member weights under test.

This is the diagnostic that found the v1.4.3 defect (ADR-053): twelve equal members -- the
widest ensemble the API accepts -- drifted to a largest total of ``0.999996``, which counted
as an interior boundary, so the default grid was thirteen points long and the twelve-point
cap rejected the documented "leave thresholds blank" path with a 422.

Run it from the backend directory:

    .venv\\Scripts\\python.exe scripts\\probe_sweep_grid.py
The behaviour it checks is now locked by ``tests/test_ensemble_sweep.py``; this script is
kept so the matrix can be re-read by hand when member weights or the cap change.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.ensemble import (  # noqa: E402
    MAX_SWEEP_THRESHOLDS,
    _coalition_totals,
    _default_thresholds_for,
)

_CASES: dict[str, list[float]] = {
    "2 equal": [1.0, 1.0],
    "5 equal": [1.0] * 5,
    "12 equal (max members)": [1.0] * 12,
    "3 weights 1/2/3": [1.0, 2.0, 3.0],
    "4 weights 1/2/3/4": [1.0, 2.0, 3.0, 4.0],
    "5 weights 1..5": [1.0, 2.0, 3.0, 4.0, 5.0],
    "6 weights 1..6": [float(i) for i in range(1, 7)],
    "6 distinct primes": [1.0, 2.0, 3.0, 5.0, 7.0, 11.0],
    "7 distinct primes": [1.0, 2.0, 3.0, 5.0, 7.0, 11.0, 13.0],
}


def _stub_members(weights: list[float]) -> list[Any]:
    """``_default_thresholds_for`` only reads ``weight``, so no specs are needed."""

    class _Stub:
        def __init__(self, weight: float) -> None:
            self.weight = weight

    return [_Stub(weight) for weight in weights]


def main() -> int:
    print(f"MAX_SWEEP_THRESHOLDS = {MAX_SWEEP_THRESHOLDS}\n")

    print("--- 12 equal members: the widest ensemble, where the drift showed ---")
    totals = _coalition_totals([1.0 / 12] * 12)
    grid = _default_thresholds_for(_stub_members([1.0] * 12))
    print(f"  totals ({len(totals)}): {totals}")
    print(f"  default grid ({len(grid)}): {grid}")
    print(f"  fits the budget: {len(grid) <= MAX_SWEEP_THRESHOLDS}")

    print("\n--- the rest of the matrix ---")
    over_budget = 0
    for name, weights in _CASES.items():
        grid = _default_thresholds_for(_stub_members(weights))
        if len(grid) > MAX_SWEEP_THRESHOLDS:
            over_budget += 1
        verdict = "OK" if len(grid) <= MAX_SWEEP_THRESHOLDS else "OVER BUDGET (actionable 422)"
        sample = grid[:3] if len(grid) > 3 else grid
        print(f"  {name:24s} grid={len(grid):4d} first={sample}  {verdict}")

    print("\n--- no coalition total may exceed 1.0 or land just under it ---")
    suspicious = [t for t in totals if t > 1.0 or 0.9999 < t < 1.0]
    print(f"  suspicious totals: {suspicious}")

    if suspicious or len(grid) > MAX_SWEEP_THRESHOLDS:
        print("\nFAILED: the exact grid is still drifting or still over budget")
        return 1
    print(f"\nOK: {over_budget} case(s) legitimately need an explicit threshold list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
