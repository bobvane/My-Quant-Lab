"""Smoke check for the bundled example strategies.

Run from ``backend/``::

    DATABASE_URL=sqlite+pysqlite:///:memory: MARKET_DATA_PROVIDER=synthetic \
        python scripts/check_examples.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.providers import get_market_data_provider  # noqa: E402
from app.research.engine import run_backtest  # noqa: E402
from app.strategies.dsl import StrategySpec  # noqa: E402
from app.strategies.validator import validate_strategy  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "strategies"


def main() -> int:
    provider = get_market_data_provider()
    end = dt.datetime.now(tz=dt.timezone.utc)
    bars = provider.get_ohlcv("DEMO-AAPL", "1d", end - dt.timedelta(days=1200), end)
    print(f"bars: {len(bars)}")

    failures = 0
    for path in sorted(EXAMPLES.glob("*.json")):
        dsl = json.loads(path.read_text(encoding="utf-8"))
        spec = StrategySpec.model_validate(dsl)
        report = validate_strategy(spec)
        result = run_backtest(
            spec, bars, strategy_version=f"{spec.strategy.id}@{spec.strategy.version}"
        )
        metrics = result.metrics
        status = "OK " if report.is_valid else "BAD"
        if not report.is_valid:
            failures += 1
        print(
            f"{status} {path.name}: trades={metrics['number_of_trades']} "
            f"return={metrics['total_return']} drawdown={metrics['max_drawdown']} "
            f"sharpe={metrics['sharpe']} hash={result.result_hash[:12]}"
        )
        for issue in report.issues:
            print(f"     [{issue.severity}] {issue.code}: {issue.message}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
