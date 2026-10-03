"""A workflow that names a moving target, or accepts a missing report, is a claim.

Three classes of defect live in `.github/workflows/` and none of them shows up as a
failed run:

* `uses: actions/checkout@v7` names a tag. A tag is a pointer its owner -- or anyone
  who compromises that repository -- can move to different code, and these workflows
  run with `packages: write` (they publish images) and, for the release, with a token
  that can mint releases. A commit SHA cannot be moved (ADR-101).
* `if-no-files-found: ignore` on an `if: always()` upload step turns "the coverage
  report exists" into a green step even when the tests died before writing it, and no
  coverage floor existed anywhere, so the number the artifact carries was never
  enforced (ADR-102).
* A coverage floor only holds if the number it compares is the number it prints:
  pytest-cov compares `round(total, precision)`, and its default precision of 0
  rounded 85.96% up to 86 -- so `--cov-fail-under=86` passed a run that printed
  `FAIL Required test coverage of 86% not reached` (ADR-105).

These guards read the workflow text. They can prove what the file says; they cannot
prove how a runner behaves (ADR-068, ADR-075).
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"

#: `uses:` may name a local action (`./.github/actions/x`), a container
#: (`docker://image`) or a published action. Only the last one has a tag to move.
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<target>\S+)(?P<rest>.*)$", re.MULTILINE)
_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _uses(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every `uses:` target in one workflow, with its line number.

    Comment lines are prose, not configuration -- the workflows explain their own
    decisions in comments, and this guard must fail on a breach, not on the note
    that documents the rule.
    """

    found: list[tuple[int, str]] = []
    for number, line in enumerate(_text(path).splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        match = _USES.match(line)
        if match:
            found.append((number, match.group("target")))
    return found


def test_every_published_action_is_pinned_to_a_commit() -> None:
    """`@v7` is a name somebody else can repoint after this commit is written."""

    offenders: list[str] = []
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, target in _uses(path):
            if target.startswith("./") or target.startswith("docker://"):
                continue
            seen += 1
            if not _SHA.match(target):
                offenders.append(f"{path.name}:{number}: {target}")
    assert seen >= 20, f"only {seen} published actions found; the scan broke"
    assert not offenders, (
        "these actions are referenced by a mutable name instead of a commit SHA, so the "
        f"code that runs is whatever that name points at today (ADR-101): {offenders}"
    )


def test_the_pinned_actions_say_which_version_they_were() -> None:
    """A 40-hex SHA on its own is unreadable; the version belongs in the comment.

    Dependabot and a human both need to see what `@<sha>` used to be, so every pin
    carries the tag it was resolved from.
    """

    unpinned = [
        f"{path.name}:{number}: {target}"
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for number, target in _uses(path)
        if _SHA.match(target)
        and path.read_text(encoding="utf-8").splitlines()[number - 1].count("#") == 0
    ]
    assert not unpinned, f"a pinned action does not name the version it came from: {unpinned}"


def test_no_workflow_can_accept_a_missing_coverage_report() -> None:
    """The step says "CI archives the coverage report"; that claim must be able to fail."""

    text = _text(CI)
    assert "if-no-files-found: ignore" not in text, (
        "the coverage upload accepts a missing report, so the artifact can be absent "
        "while the step reports success (ADR-102)"
    )
    assert "path: backend/coverage.xml" in text, "the upload no longer names the report"


def test_the_coverage_floor_means_what_the_run_prints() -> None:
    """A floor the plugin rounds before comparing is not the floor it prints (ADR-105)."""

    text = _text(CI)
    floor = re.search(r"--cov-fail-under=([\d.]+)", text)
    assert floor, "the suite measures coverage but never requires a floor (ADR-102)"
    value = float(floor.group(1))
    assert 50 <= value <= 100, f"a coverage floor of {value}% would never fail a realistic run"
    precision = re.search(r"--cov-precision=(\d+)", text)
    assert precision, (
        "pytest-cov compares `round(total, precision)` against the floor, and its default "
        "precision of 0 rounds the total up: 85.96% passed a floor of 86% on the same run "
        "that printed `FAIL Required test coverage of 86% not reached` (ADR-105)"
    )
    assert int(precision.group(1)) >= 2, (
        "a precision below 2 rounds the total to a number the report does not show, so the "
        "floor can disagree with the verdict printed next to it (ADR-105)"
    )
    assert "--cov-report=xml" in text, "the upload has no report to carry"
