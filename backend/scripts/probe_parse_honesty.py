"""Show that a Python file which did not parse is not reported as analysed.

Run it from the backend directory:

    .venv\\Scripts\\python.exe scripts\\probe_parse_honesty.py

Repository code is untrusted input, so a Python file can be downloaded
successfully and still fail to parse: Python 2 syntax, a null byte, a construct
the parser refuses. Such a file contributed nothing -- no rule, no indicator, not
even an unknown. Counting it as *parsed* is how a report claims coverage it does
not have, one layer deeper than the fetch cap (ADR-056) and the time budget
(ADR-057): read and understood are two different claims (ADR-059).

This probe analyses three in-memory trees and prints the counters and warnings
that decide whether the watcher may import without a human:

  clean        every candidate read and parsed
  unparsed     everything read, but one file does not parse
  unparsed+gap one file does not parse *and* the fetch was capped
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importer.dsl_builder import build_draft_dsl  # noqa: E402
from app.importer.extract import (  # noqa: E402
    analyze_repository_files,
    build_coverage,
    coverage_warnings,
)
from app.importer.github_client import (  # noqa: E402
    FetchCoverage,
    RepoFile,
    RepoMeta,
)

_CLEAN = "fast = ema(close, 5)\nslow = ema(close, 20)\nlong_entry = fast > slow\n"
_LEGACY = "print 'python 2 style'\n"  # SyntaxError under Python 3
_NULL_BYTE = "value = 1\x00\n"  # ValueError/SyntaxError, depending on the parser
_META = RepoMeta(
    owner="acme",
    repo="strat",
    ref="main",
    default_branch="main",
    description="demo",
    license="MIT",
    pushed_at=None,
    html_url="https://github.com/acme/strat",
)


def _file(path: str, content: str | None, reason: str | None = None) -> RepoFile:
    return RepoFile(
        path=path, size=len(content or ""), sha=path, content=content, skipped_reason=reason
    )


def _fetch(fetched: int, candidates: int) -> FetchCoverage:
    """A fetch that downloaded ``fetched`` of ``candidates`` files, no failures."""

    return FetchCoverage(
        candidate_files=candidates,
        candidate_python_files=candidates,
        attempted_files=fetched,
        downloaded_files=fetched,
        skipped_files=0,
        skipped_python_files=0,
        not_attempted_files=candidates - fetched,
        not_attempted_python_files=candidates - fetched,
        cap=fetched,
    )


def _report(label: str, files: list[RepoFile], fetch: FetchCoverage) -> None:
    findings = analyze_repository_files(files)
    _, draft_warnings = build_draft_dsl(_META, findings)
    coverage = build_coverage(fetch, findings)
    warnings = draft_warnings + coverage_warnings(coverage)

    print(f"===== {label} =====")
    print(f"  files_parsed            : {findings.files_parsed}")
    for unparsed in findings.files_unparsed:
        print(f"  files_unparsed          : {unparsed.path} ({unparsed.reason})")
    for skipped in findings.files_skipped:
        print(f"  files_skipped           : {skipped.path} ({skipped.reason})")
    print("  -- coverage --")
    for key in (
        "candidate_files",
        "attempted_files",
        "downloaded_files",
        "parsed_files",
        "unread_python_files",
        "unparsed_python_files",
        "complete",
    ):
        print(f"  {key:<24}: {coverage[key]}")
    blocked = int(coverage["unread_python_files"]) + int(coverage["unparsed_python_files"])
    if blocked:
        print(f"  watcher                 : refuses (rules unknown in {blocked} Python file(s))")
    else:
        print("  watcher                 : may import unattended")
    print("  -- warnings --")
    for warning in warnings:
        print(f"    {warning}")
    if not warnings:
        print("    (none: everything was read and understood)")
    print()


def main() -> int:
    clean = [_file("good.py", _CLEAN), _file("also_good.py", _CLEAN)]
    _report("clean", clean, _fetch(2, 2))

    unparsed_files = [*clean, _file("legacy.py", _LEGACY), _file("nul.py", _NULL_BYTE)]
    _report("unparsed", unparsed_files, _fetch(4, 4))

    gapped = [*clean, _file("legacy.py", _LEGACY)]
    _report("unparsed+gap", gapped, _fetch(3, 12))

    # The probe makes a claim, so it checks it: a file that did not parse must not
    # appear among the files the report says it parsed.
    findings = analyze_repository_files(unparsed_files)
    assert "legacy.py" not in findings.files_parsed, "an unparsed file was reported as parsed"
    assert "nul.py" not in findings.files_parsed, "an unparsed file was reported as parsed"
    assert {f.path for f in findings.files_unparsed} == {"legacy.py", "nul.py"}
    assert not findings.unknowns, "an unparsed file leaked into the mapping report"
    print(
        "Read and understood are two different claims: a file that did not parse is\n"
        "reported as unparsed, contributes nothing to the draft, and stops the watcher\n"
        "from importing unattended instead of looking like a file that was analysed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
