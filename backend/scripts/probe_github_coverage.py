"""Show what a client can learn about the *coverage* of a GitHub analysis.

Run it from the backend directory:

    .venv\\Scripts\\python.exe scripts\\probe_github_coverage.py

The importer's whole premise is that repository code is untrusted input: the human
reviews the findings and the DSL before anything is imported. That review is only
meaningful if the report says how much of the repository it actually looked at.

This probe wires a stub client around a 30-candidate tree (20 ``.py`` + 10 ``.md``),
makes one file too large for the byte limit and one fail to download, and then prints
the coverage block and warnings the API now returns -- for a narrow cap (most
candidates never attempted) and a wide one (non-Python files inside the cap).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importer.dsl_builder import build_draft_dsl  # noqa: E402
from app.importer.extract import (  # noqa: E402
    analyze_repository_files,
    build_coverage,
    coverage_warnings,
)
from app.importer.github_client import (  # noqa: E402
    MAX_FILE_BYTES,
    FetchCoverage,
    GitHubClient,
    GitHubError,
    RepoFile,
    RepoMeta,
)

_TOO_LARGE = "mod01.py"
_UNFETCHABLE = "mod02.py"
_TREE = [
    {"path": f"mod{i:02d}.py", "type": "blob", "size": 100, "sha": f"p{i}"} for i in range(20)
] + [{"path": f"doc{i}.md", "type": "blob", "size": 40, "sha": f"d{i}"} for i in range(10)]
_SOURCE = "fast = ema(close, 5)\nslow = ema(close, 20)\nlong_entry = cross_above(fast, slow)\n"


class _StubGitHub(GitHubClient):
    """Deterministic replacement: no network, one oversized file, one failing download."""

    def get_json(self, url: str) -> Any:  # type: ignore[override]
        if "/git/trees/" in url:
            return {"tree": list(_TREE), "truncated": False}
        return {
            "name": "strat",
            "default_branch": "main",
            "description": "demo",
            "license": {"spdx_id": "MIT"},
            "pushed_at": None,
            "html_url": "https://github.com/acme/strat",
        }

    def get_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:  # type: ignore[override]
        # Keep fetch_repository's size check meaningful: it reads ``size`` from here.
        return [
            {**entry, "size": MAX_FILE_BYTES + 1 if entry["path"] == _TOO_LARGE else entry["size"]}
            for entry in _TREE
        ]

    def get_text(self, url: str, *, max_bytes: int = MAX_FILE_BYTES) -> str:  # type: ignore[override]
        if url.endswith(_UNFETCHABLE):
            raise GitHubError("network error fetching this file")
        return _SOURCE


def _scenario(
    cap: int, seconds: float | None = None
) -> tuple[RepoMeta, list[RepoFile], FetchCoverage]:
    return _StubGitHub().fetch_repository(
        "https://github.com/acme/strat", max_files=cap, max_seconds=seconds
    )


def main() -> int:
    print(
        f"tree candidates matching STRATEGY_EXTENSIONS: {len(_TREE)} "
        f"(20 .py + 10 .md), MAX_FILE_BYTES={MAX_FILE_BYTES}"
    )
    print(f"{_TOO_LARGE} exceeds the byte limit; {_UNFETCHABLE} raises on download.")
    print()

    for cap, seconds in ((5, None), (22, None), (20, 0.0)):
        meta, files, fetch = _scenario(cap, seconds)
        findings = analyze_repository_files(files)
        _, draft_warnings = build_draft_dsl(meta, findings)
        coverage = build_coverage(fetch, findings)
        warnings = draft_warnings + coverage_warnings(coverage)

        label = f"cap = {cap}" + (f", max_seconds = {seconds}" if seconds is not None else "")
        print(f"===== {label} =====")
        print("-- fetch_repository returned --")
        for repo_file in files:
            state = "content" if repo_file.content is not None else "skipped"
            print(f"  {repo_file.path:<12} {state:<8} reason={repo_file.skipped_reason!r}")
        print("-- what the analyzer kept --")
        print(f"  files_parsed      : {findings.files_parsed}")
        print(f"  files_inventoried : {findings.files_inventoried}  <- listed, not parsed")
        for skipped in findings.files_skipped:
            print(f"  files_skipped     : {skipped.path} ({skipped.reason})")
        print("-- coverage block (what the response now says) --")
        for key, value in coverage.items():
            print(f"  {key:<24} {value}")
        print("-- warnings --")
        for warning in warnings:
            print(f"  {warning}")
        if not warnings:
            print("  (none: the whole repository inside the cap was read)")
        print()

    print(
        "The response can now distinguish 'analysed 3 files' from 'analysed 3 of 30',\n"
        "it says why each skipped file was skipped, it no longer counts inventoried\n"
        "non-Python files as parsed, and a fetch that ran out of its time budget says so\n"
        "instead of blaming the cap."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
