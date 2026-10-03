"""Offline probe: how long does `/health` make a caller wait? (ADR-069)

`/health` answers three dependency questions in a row — PostgreSQL, Redis and
"are the Celery workers there". Rusty ones (a bare install with no Redis, no
broker) used to hold the whole response for ten seconds or more: the dashboard's
first paint waited on it, and a probe that answers after the caller gave up is
not an answer.

Each probe function is timed exactly as the endpoint calls it. Every probe must
come back inside `PROBE_BUDGET_SECONDS`, and the sum inside `TOTAL_BUDGET_SECONDS`.
The first dependency lookup in a process also pays the resolver, so the probes run
once as a warm-up and only the second round is graded — an unbounded wait shows up
in both rounds, a cold resolver only in the first.

    python scripts/probe_health_latency.py

Set `DATABASE_URL`/`REDIS_URL`/`CELERY_BROKER_URL` to point the probe somewhere
else; the defaults come from the app settings.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.api.routers.health import _check_redis, _check_workers  # noqa: E402
from app.core.config import settings  # noqa: E402

#: One probe may not take longer than this, and all of them together not longer
#: than the second number. Both are deliberately generous: the point is to catch
#: an *unbounded* wait (an unreachable broker used to cost 9-12 seconds here),
#: not to measure a fast machine.
PROBE_BUDGET_SECONDS = 3.0
TOTAL_BUDGET_SECONDS = 5.0


def _checked(label: str, func) -> tuple[str, float, str, list[str]]:
    started = time.perf_counter()
    try:
        state = func()
    except Exception as exc:  # pragma: no cover - diagnostics
        state = f"raised {type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    failures: list[str] = []
    if elapsed > PROBE_BUDGET_SECONDS:
        failures.append(
            f"{label}: took {elapsed:.2f}s, budget is {PROBE_BUDGET_SECONDS:.1f}s "
            "(the probe is not bounded)"
        )
    return label, elapsed, str(state), failures


def _check_database() -> str:
    """Same question as the endpoint's `_check_database`, on its own engine."""

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return "connected"
    finally:
        engine.dispose()


PROBES = (
    ("database", _check_database),
    ("redis", _check_redis),
    ("workers", _check_workers),
)


def main() -> int:
    print(f"database_url      = {settings.database_url}")
    print(f"redis_url         = {settings.redis_url}")
    print(f"celery_broker_url = {settings.celery_broker_url}")

    for label, func in PROBES:  # warm-up round, not graded
        _checked(label, func)

    failures: list[str] = []
    total = 0.0
    for label, func in PROBES:
        name, elapsed, state, problems = _checked(label, func)
        failures.extend(problems)
        total += elapsed
        print(f"{name:10s} -> {state:12s} in {elapsed:6.2f}s")

    print(f"{'total':10s} -> {'':12s} in {total:6.2f}s")
    if total > TOTAL_BUDGET_SECONDS:
        failures.append(
            f"/health would make a caller wait {total:.2f}s, budget is {TOTAL_BUDGET_SECONDS:.1f}s"
        )

    if failures:
        print("\nRESULT: failures")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: every dependency probe answers inside its budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
