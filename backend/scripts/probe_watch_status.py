"""Live probe: does the watcher store the outcome it reports? (ADR-058)

Runs one watched GitHub source through `check_source` twice — once at its current
commit, once after rewinding `current_commit` — and prints both the returned status
and the value that landed in `last_import_status`, plus the snapshot that explains it.

This talks to the real network, so it is slow: the second pass usually stops at the
fetch budget (`DEFAULT_FETCH_BUDGET_SECONDS`) and reports an incomplete read.

    python scripts/probe_watch_status.py --source-id 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import session_scope  # noqa: E402
from app.domain.models import GitHubSnapshot, GitHubSource  # noqa: E402
from app.workers import tasks  # noqa: E402

REWOUND_COMMIT = "0" * 40


def _latest_snapshot(db, source_id: int) -> GitHubSnapshot | None:
    return (
        db.query(GitHubSnapshot)
        .filter(GitHubSnapshot.source_id == source_id)
        .order_by(GitHubSnapshot.id.desc())
        .first()
    )


def _describe(snapshot: GitHubSnapshot | None, indent: str = "                ") -> None:
    if snapshot is None:
        print(f"{indent}no snapshot recorded")
        return
    extraction = snapshot.extraction_json or {}
    print(f"{indent}commit={snapshot.commit[:12]}")
    print(f"{indent}imported={extraction.get('imported')} reason={extraction.get('reason')!r}")
    print(f"{indent}transient={extraction.get('transient')}")
    coverage = extraction.get("coverage") or {}
    print(
        f"{indent}coverage="
        f"{coverage.get('attempted_files')}/{coverage.get('candidate_files')} read, "
        f"unread_python={coverage.get('unread_python_files')}, "
        f"budget_exhausted={coverage.get('budget_exhausted')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, default=1, help="watched source to probe")
    args = parser.parse_args(argv)

    with session_scope() as db:
        source = db.query(GitHubSource).filter(GitHubSource.id == args.source_id).one()
        print(
            f"before        : commit={str(source.current_commit)[:12]} "
            f"status={source.last_import_status!r}"
        )

        status = tasks.check_source(db, source)
        print(f"same commit   : returned={status!r} stored={source.last_import_status!r}")
        print(f"                current_commit={str(source.current_commit)[:12]}")

        source.current_commit = REWOUND_COMMIT
        db.flush()
        status = tasks.check_source(db, source)
        print(f"new commit    : returned={status!r} stored={source.last_import_status!r}")
        print(f"                current_commit advanced={source.current_commit != REWOUND_COMMIT}")

        _describe(_latest_snapshot(db, source.id))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
