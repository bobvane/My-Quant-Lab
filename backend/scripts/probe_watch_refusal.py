"""Offline probe: does the watcher refuse a draft it cannot import? (ADR-062)

Runs the real `check_source` against an in-memory SQLite database with a stub
GitHub client, so the outcome a scheduled run records can be read without a
network or a server: what the import gate answers, what the refused run stores,
which commit is now waiting for a human, what asking again costs, and what a
newer commit does to that wait.

    python scripts/probe_watch_refusal.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.db import Base  # noqa: E402
from app.data.strategy_service import next_version, strategy_dsl_problem  # noqa: E402
from app.domain.models import GitHubSnapshot, GitHubSource, Strategy, StrategyVersion  # noqa: E402
from app.importer.github_client import FetchCoverage  # noqa: E402
from app.importer.extract import AnalysisResult  # noqa: E402

REPO = "https://github.com/acme/strat"

SAMPLE_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "probe", "name": "Probe", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}
# What the importer really builds: exit rules are never invented (docs/05 §4.3).
EXIT_LESS_DRAFT = {**SAMPLE_DSL, "exit": {}}
# Parses, but the validator refuses it — the quiet half of the gate.
FUTURE_DRAFT = {
    **SAMPLE_DSL,
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "future_close"}]}},
}


def _complete_coverage() -> FetchCoverage:
    return FetchCoverage(
        candidate_files=2,
        candidate_python_files=1,
        attempted_files=2,
        downloaded_files=2,
        skipped_files=0,
        skipped_python_files=0,
        not_attempted_files=0,
        not_attempted_python_files=0,
        cap=30,
    )


class _StubGitHub:
    """A client with a fixed head that counts how often it is asked to fetch."""

    head = "newsha"
    fetches = 0

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    def get_head_commit(self, owner: str, repo: str) -> str:
        return type(self).head

    def fetch_repository(self, repo_url, ref=None, *, max_files=30):  # noqa: ANN001
        type(self).fetches += 1
        return (object(), [], _complete_coverage())


def _check_gate() -> list[str]:
    failures: list[str] = []
    print("== the gate both import paths share ==")
    for label, draft in (
        ("a complete draft", SAMPLE_DSL),
        ("a draft with no exit rules", EXIT_LESS_DRAFT),
        ("a draft that reads future data", FUTURE_DRAFT),
    ):
        problem = strategy_dsl_problem(draft)
        print(f"  {label}: {problem or 'importable'}")
        if label.startswith("a complete") and problem is not None:
            failures.append(f"a valid draft was refused: {problem}")
        if not label.startswith("a complete") and not problem:
            failures.append(f"{label} was waved through")

    print("== the ledger's numbering, not the release cadence ==")
    for existing, expected in ((["1.0.9"], "1.0.10"), (["1.9.0", "1.10.0"], "1.10.1")):
        got = next_version(existing)
        ok = got == expected
        print(f"  {'ok  ' if ok else 'FAIL'} {existing} -> {got} (expected {expected})")
        if not ok:
            failures.append(f"next_version({existing}) == {got}, expected {expected}")
    return failures


def _check_watcher() -> list[str]:
    """Run the real check_source three times: refused, asked again, superseded."""
    failures: list[str] = []
    import app.importer as importer
    from app.workers.tasks import check_source

    state = {"draft": EXIT_LESS_DRAFT}
    original = (importer.GitHubClient, importer.analyze_repository_files, importer.build_draft_dsl)
    importer.GitHubClient = _StubGitHub
    importer.analyze_repository_files = lambda files: AnalysisResult()
    importer.build_draft_dsl = lambda meta, findings: (state["draft"], [])

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    try:
        source = GitHubSource(repository_url=REPO, current_commit="oldsha", is_watched=True)
        db.add(source)
        db.flush()
        strategy = Strategy(name="Probe Watch", slug="probe-watch", source_url=REPO)
        db.add(strategy)
        db.flush()
        db.add(
            StrategyVersion(
                strategy_id=strategy.id,
                version="1.0.0",
                dsl_json={**SAMPLE_DSL, "execution": {"fill_model": "next_bar_open", "fee_bps": 0}},
                immutable_hash="x" * 64,
            )
        )
        db.commit()

        print("== run 1: a new commit whose draft cannot be imported ==")
        status = check_source(db, source)
        db.commit()
        snapshot = db.query(GitHubSnapshot).one()
        print(f"  status: {status}")
        print(f"  stored status: {source.last_import_status}")
        print(f"  current_commit: {source.current_commit}")
        print(f"  pending_review_commit: {source.pending_review_commit}")
        print(f"  snapshot reason: {snapshot.extraction_json['reason']}")
        print(f"  snapshot detail: {snapshot.extraction_json['detail']}")
        print(f"  versions: {[v.version for v in db.query(StrategyVersion).all()]}")
        if status != "review_required" or source.pending_review_commit != "newsha":
            failures.append(f"a refused draft ended as {status!r}")
        if db.query(StrategyVersion).count() != 1:
            failures.append("a draft that cannot be imported was written anyway")
        if "at least one exit rule is required" not in snapshot.extraction_json["detail"]:
            failures.append("the snapshot does not say why the import stopped")

        print("== run 2: the same commit, still waiting for a human ==")
        before = _StubGitHub.fetches
        status = check_source(db, source)
        db.commit()
        print(f"  status: {status}, fetches: {_StubGitHub.fetches - before}")
        print(f"  snapshots on record: {db.query(GitHubSnapshot).count()}")
        if status != "review_required" or _StubGitHub.fetches != before:
            failures.append("the pending review was re-fetched instead of reported")
        if db.query(GitHubSnapshot).count() != 1:
            failures.append("asking again wrote a second snapshot for the same wait")

        print("== run 3: a newer commit supersedes the review ==")
        _StubGitHub.head = "newer"
        state["draft"] = SAMPLE_DSL  # this one a human could have written
        before = _StubGitHub.fetches
        status = check_source(db, source)
        db.commit()
        print(f"  status: {status}, fetches: {_StubGitHub.fetches - before}")
        print(f"  current_commit: {source.current_commit}")
        print(f"  pending_review_commit: {source.pending_review_commit}")
        print(f"  versions: {[v.version for v in db.query(StrategyVersion).all()]}")
        if status != "imported" or source.pending_review_commit is not None:
            failures.append(f"a newer commit did not clear the wait: {status!r}")
        if _StubGitHub.fetches == before:
            failures.append("a newer commit was not fetched")
    finally:
        importer.GitHubClient, importer.analyze_repository_files, importer.build_draft_dsl = original
        db.close()
        engine.dispose()
    return failures


def main() -> int:
    failures = _check_gate() + _check_watcher()
    if failures:
        print("\nRESULT: failures")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: an unimportable draft is refused, recorded, and waited on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
