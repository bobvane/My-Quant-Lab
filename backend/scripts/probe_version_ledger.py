"""Offline probe: does the version ledger own numbering? (ADR-061)

Runs `next_version` and `strategy_version_plan` against an in-memory SQLite
database and prints what each answers, so the behaviour the API exposes can be
read without starting a server.

    python scripts/probe_version_ledger.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.db import Base  # noqa: E402
from app.data.strategy_service import (  # noqa: E402
    create_strategy_version,
    next_version,
    strategy_version_plan,
)
from app.domain.models import Strategy  # noqa: E402

SAMPLE_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "probe", "name": "Probe", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

# (existing versions, expected next) — the 1.9.0/1.10.0 pair is the one a string
# comparison gets wrong.
CASES: list[tuple[list[str], str]] = [
    ([], "1.0.0"),
    (["1.0.0"], "1.0.1"),
    (["1.0.0", "1.0.9"], "1.0.10"),
    (["1.2.3", "1.9.0", "1.10.0"], "1.10.1"),
    (["2.0.0"], "2.0.1"),
]


def _check_pure() -> list[str]:
    failures: list[str] = []
    print("== next_version ==")
    for existing, expected in CASES:
        got = next_version(existing)
        ok = got == expected
        print(f"  {'ok  ' if ok else 'FAIL'} {existing} -> {got} (expected {expected})")
        if not ok:
            failures.append(f"next_version({existing}) == {got}, expected {expected}")

    print("== next_version refuses an unreadable version ==")
    try:
        next_version(["v2-beta"])
    except ValueError as exc:
        print(f"  ok   refused: {exc}")
    else:
        print("  FAIL it invented a version for 'v2-beta'")
        failures.append("next_version(['v2-beta']) did not refuse")
    return failures


def _check_ledger() -> list[str]:
    failures: list[str] = []
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    print("== a name with no strategy ==")
    plan = strategy_version_plan(session, "Probe Ledger")
    print(f"  {plan}")
    if plan["strategy_id"] is not None or plan["versions"] or plan["next_version"] != "1.0.0":
        failures.append(f"unexpected empty-ledger plan: {plan}")

    strategy = Strategy(name="Probe Ledger", slug="probe-ledger", source_type="github")
    session.add(strategy)
    session.flush()

    print("== after 1.0.0 and an assigned 1.0.1 ==")
    create_strategy_version(session, strategy, version="1.0.0", dsl=SAMPLE_DSL)
    assigned = strategy_version_plan(session, "Probe Ledger")["next_version"]
    print(f"  the ledger would assign {assigned} after 1.0.0")
    if assigned != "1.0.1":
        failures.append(f"the ledger would assign {assigned} after 1.0.0")
    create_strategy_version(session, strategy, version=assigned, dsl=SAMPLE_DSL)
    plan = strategy_version_plan(session, "Probe Ledger")
    print(f"  {plan}")
    if plan["versions"] != ["1.0.0", "1.0.1"] or plan["next_version"] != "1.0.2":
        failures.append(f"unexpected ledger after two versions: {plan}")
    if plan["strategy_id"] != strategy.id:
        failures.append(f"plan lost the strategy id: {plan}")

    print("== after a hand-named version nobody can increment ==")
    create_strategy_version(session, strategy, version="v2-beta", dsl=SAMPLE_DSL)
    plan = strategy_version_plan(session, "Probe Ledger")
    print(f"  {plan}")
    if plan["can_assign"] or plan["next_version"] is not None or "v2-beta" not in plan["reason"]:
        failures.append(f"the ledger should refuse here: {plan}")

    session.close()
    engine.dispose()
    return failures


def main() -> int:
    failures = _check_pure() + _check_ledger()
    if failures:
        print("\nRESULT: failures")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: the ledger owns numbering and refuses what it cannot read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
