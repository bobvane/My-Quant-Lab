"""Guards for the self-check scripts in `scripts/`: a verdict must reach the shell.

`scripts/Test-WebUi.ps1` and `scripts/Test-NasDeployment.ps1` end in an exit code.
`scripts/Test-EnsembleAttribution.ps1` used to only *print* `INVARIANTS: OK/FAILED`
(ADR-073), so a run in which every invariant failed still exited 0 — the verdict lived
in console text that no caller reads. The second half of the same defect is a probe that
prints an identity without asserting it: it forwarded the sync report and both member
runs to `Out-Null` and rendered a "backtest summary identity" table nobody checked.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SELF_CHECKS = sorted(SCRIPTS.glob("Test-*.ps1"))
ENSEMBLE = SCRIPTS / "Test-EnsembleAttribution.ps1"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _joined(text: str) -> str:
    """Fold PowerShell line continuations so a statement is one line again."""

    return re.sub(r"`\r?\n\s*", " ", text)


def _window(text: str, needle: str, before: int = 14, after: int = 1) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith(needle):
            return "\n".join(lines[max(0, index - before) : index + after + 1])
    raise AssertionError(f"{needle!r} not found")


def test_there_are_self_checks_to_guard():
    names = {path.name for path in SELF_CHECKS}
    assert {
        "Test-EnsembleAttribution.ps1",
        "Test-NasDeployment.ps1",
        "Test-WebUi.ps1",
    } <= names


def test_every_self_check_can_report_failure_as_an_exit_code():
    """A probe that cannot fail gates nothing, however good its assertions look."""

    for path in SELF_CHECKS:
        text = _text(path)
        assert "exit 1" in text, f"{path.name} prints a verdict but cannot fail"
        assert "exit 0" in text, f"{path.name} has no success exit code"


def test_the_ensemble_probe_decides_its_exit_from_its_collected_failures():
    text = _text(ENSEMBLE)
    assert "$failures" in text, "the probe must collect failures instead of only printing them"
    assert "$failures" in _window(text, "exit 1"), "the failure exit is not driven by the verdict"
    assert "$failures" in _window(text, "exit 0"), "the success exit ignores the verdict"
    assert text.rstrip().endswith("exit 0"), "the verdict must be the last thing the script does"


def test_the_ensemble_probe_asserts_the_data_it_compares():
    text = _text(ENSEMBLE)
    # The sync report is evidence: `inserted: 0` is legitimate (syncing is idempotent),
    # a report without a series is not — a probe over an empty dataset verifies nothing.
    assert "market-data/sync" in text
    assert "series_id" in text
    # A member run is only comparable evidence if it completed, on the probe's own
    # series, with a reproducible result hash (backtest reproducibility is a red line).
    assert "result_hash" in text
    assert "'^[0-9a-f]{64}$'" in text
    assert "dataset_version_id" in text
    assert "bars_evaluated" in text


def test_the_ensemble_probe_does_not_throw_away_the_responses_it_prints():
    joined = _joined(_text(ENSEMBLE))
    for request in ("market-data/sync", '/backtests" -Method Post'):
        matches = [line for line in joined.splitlines() if request in line and "Out-Null" in line]
        assert not matches, f"{request} response is discarded instead of asserted: {matches}"


def test_the_ensemble_probe_still_checks_the_sweep_contract():
    """The rewrite that added the verdict must not drop the invariants it had."""

    text = _text(ENSEMBLE)
    for marker in ("max_thresholds", "possible_votes", "effective_vote", "engine_version"):
        assert marker in text
