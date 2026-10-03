"""Offline probe: is taking money out of a paper account a trading loss? (ADR-066)

Funds a real `PaperAccount` through the real `fund_account` endpoint against an
in-memory SQLite database, then reads the real `account_equity` and
`account_performance` endpoints, and prints the number the accounts table shows
(`(cash - net_deposits) / net_deposits`, see `frontend/src/views/PaperView.vue`).

A withdrawal used to lower only `cash`: the baseline stayed where it was, an
account nobody had traded was published as -40%, and an account that had *made*
1,000 was published as -30%. `final_equity` kept describing money that had
already left the account.

    python scripts/probe_paper_contributions.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.routers.paper import account_equity, account_performance, fund_account  # noqa: E402
from app.api.schemas import PaperFundRequest  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.domain.models import Asset, PaperAccount, PaperTrade  # noqa: E402

COUNTER = {"n": 0}


def _account(db, *, cash: float, realized: float = 0.0) -> PaperAccount:
    COUNTER["n"] += 1
    n = COUNTER["n"]
    account = PaperAccount(name=f"probe-{n}", initial_cash=cash, cash=cash)
    db.add(account)
    db.flush()
    if realized:
        asset = Asset(symbol=f"PROBE{n}", asset_class="stock")
        db.add(asset)
        db.flush()
        db.add(
            PaperTrade(
                account_id=account.id,
                asset_id=asset.id,
                direction="LONG",
                entry_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                entry_price=10.0,
                exit_time=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                exit_price=11.0,
                quantity=1.0,
                pnl=realized,
            )
        )
        account.cash = float(cash) + realized
    db.commit()
    return account


def _row(db, account: PaperAccount, label: str) -> tuple[dict, list[str]]:
    equity = account_equity(account.id, db)
    perf = account_performance(account.id, db)
    cash = equity["cash"]
    net_deposits = equity["net_deposits"]
    failures: list[str] = []
    if equity["net_deposits"] != perf["net_deposits"]:
        failures.append(f"{label}: equity and performance disagree on net_deposits")
    # final_equity = net deposits + realised P&L; with no open position that is cash.
    if abs(perf["final_equity"] - cash) > 1e-9:
        failures.append(
            f"{label}: final_equity {perf['final_equity']} describes money the account "
            f"no longer holds (cash {cash})"
        )
    if net_deposits > 0 and perf["closed_trades"] > 0:
        pnl_pct = (cash - net_deposits) / net_deposits
        if perf["metrics"]["total_return"] is None:
            failures.append(f"{label}: no return published although net deposits are positive")
        elif abs(perf["metrics"]["total_return"] - pnl_pct) > 1e-9:
            failures.append(
                f"{label}: published return {perf['metrics']['total_return']} != P&L on net "
                f"deposits {pnl_pct}"
            )
    elif perf["metrics"]["total_return"] is not None:
        # With no closed trade there is nothing to compute a return from (a one-point
        # equity curve), so only the no-denominator case is a failure here.
        if net_deposits <= 0:
            failures.append(
                f"{label}: published return {perf['metrics']['total_return']} although net "
                f"deposits are {net_deposits} (no denominator)"
            )
    return (
        {
            "case": label,
            "cash": cash,
            "net_deposits": net_deposits,
            "pnl_pct": None
            if net_deposits <= 0
            else round((cash - net_deposits) / net_deposits, 4),
            "final_equity": perf["final_equity"],
            "total_return": perf["metrics"]["total_return"],
        },
        failures,
    )


def _check_contributions() -> list[str]:
    failures: list[str] = []
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    cases: list[tuple[str, float, float, float, float | None]] = [
        # label, opening cash, realised P&L, funding amount, expected P&L fraction
        ("flat 10,000, untouched", 10_000.0, 0.0, 0.0, 0.0),
        ("flat 10,000, deposit +5,000", 10_000.0, 0.0, 5_000.0, 0.0),
        ("flat 10,000, withdraw -4,000", 10_000.0, 0.0, -4_000.0, 0.0),
        ("10,000 + 1,000 realised, withdraw -4,000", 10_000.0, 1_000.0, -4_000.0, 1_000 / 6_000),
        ("flat 10,000, withdraw everything", 10_000.0, 0.0, -10_000.0, None),
        ("10,000 + 1,000 realised, withdraw -11,000", 10_000.0, 1_000.0, -11_000.0, None),
    ]
    try:
        for label, opening, realized, amount, expect in cases:
            account = _account(db, cash=opening, realized=realized)
            if amount:
                fund_account(account.id, PaperFundRequest(amount=amount), db)
            row, problems = _row(db, account, label)
            failures.extend(problems)
            print(
                f"{row['case']:42s} cash={row['cash']:>9.2f} "
                f"net_deposits={row['net_deposits']:>9.2f} "
                f"pnl={str(row['pnl_pct']):>8s} final_equity={row['final_equity']:>9.2f} "
                f"total_return={str(row['total_return']):>8s}"
            )
            if expect is None:
                if row["pnl_pct"] is not None:
                    failures.append(f"{label}: a P&L percentage was published with no denominator")
            elif row["pnl_pct"] is None or abs(row["pnl_pct"] - expect) > 1e-4:
                failures.append(f"{label}: P&L {row['pnl_pct']}, expected {round(expect, 4)}")
    finally:
        db.close()
        engine.dispose()
    return failures


def main() -> int:
    failures = _check_contributions()
    if failures:
        print("\nRESULT: failures")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: money in and money out move the baseline, and never the P&L")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
