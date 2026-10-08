"""Guards for the scripts and workflows that are supposed to notice failures (ADR-090).

A check that cannot fail is decoration: it reports health nobody observed. v1.6.4's
script/CI audit found eight of them, all in the same shape -- a step that prints a
result instead of asserting one, a probe that asks a question nobody answers, a
sibling that quietly disagrees with its twin, or a variable that is never set so the
branch it guards never runs.

These tests read files as text. They pin the *shape* of each fix: the verdict must
read the thing it judges, and the failure path must exist. They cannot prove a shell
script runs correctly -- the behavioural proof is a live run (`scripts/verify-stack.sh`
against a started stack) or an actual CI run.
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

NAS_CHECK = SCRIPTS / "Test-NasDeployment.ps1"
VERIFY_STACK = SCRIPTS / "verify-stack.sh"
LOCAL_STACK = SCRIPTS / "Start-LocalStack.ps1"
FRONTEND_CHECKS = SCRIPTS / "Invoke-FrontendChecks.ps1"
VERSION_SH = SCRIPTS / "version.sh"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"
README = REPO_ROOT / "README.md"
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _uncommented(text: str) -> str:
    """Drop whole-line comments, so a comment about the old probe is not evidence."""

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _step_body(text: str, name_fragment: str) -> str:
    """Body of a PowerShell `Step '...' { ... }` block, up to the next Step."""

    matches = list(re.finditer(r"^\s*Step\s+(?:'([^']*)'|\"([^\"]*)\")\s*\{", text, re.MULTILINE))
    for index, match in enumerate(matches):
        name = match.group(1) or match.group(2)
        if name_fragment in name:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[match.end() : end]
    raise AssertionError(
        f"no step matches {name_fragment!r}: {[m.group(1) or m.group(2) for m in matches]}"
    )


def test_the_unknown_id_step_compares_the_status_not_the_message() -> None:
    """`-match '404'` matched the step's own failure text, so HTTP 200 was reported OK."""

    body = _uncommented(_step_body(_text(NAS_CHECK), "AI 任务详情"))
    assert "Get-ApiStatus" in body, "the step no longer reads the response status"
    assert "-ne 404" in body, "the step does not compare the status against 404"
    assert "$_.Exception.Message -match" not in body, "the verdict is back to matching prose"
    assert not re.search(r"-match\s+'404'", body), "a literal 404 match is back in the step"


def test_the_shared_verdict_asks_for_dependency_health() -> None:
    """`/healthz` touches nothing, so a stack whose workers never started passed."""

    script = _uncommented(_text(VERIFY_STACK))
    assert "/api/v1/health" in script, "the verdict no longer asks the dependency-health endpoint"
    assert "/api/v1/healthz" not in script, "the verdict is back on the liveness probe"
    assert '"workers"' in script, "the verdict ignores the worker count"
    assert "healthy" in script, "the verdict ignores the dependency status"
    # The web half must keep asking what it always asked (ADR-068).
    assert "/healthz" in script and 'id="app"' in script
    assert "api_ok" in script and "web_ok" in script


def test_every_smoke_probe_asserts_something() -> None:
    """A `python3 -c` that only prints is a step that cannot fail on a wrong answer."""

    checks = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(_text(path).splitlines(), start=1):
            if "python3 -c" not in line:
                continue
            # `python3 -c` inside a command substitution extracts a value for the next
            # step; a probe that stands on its own is a verdict and has to assert.
            if "$(" in line.split("python3 -c")[0]:
                continue
            checks += 1
            assert "assert" in line, (
                f"{path.name}:{number} prints a result instead of asserting one"
            )
    assert checks >= 3, f"only {checks} standalone probes found; the smoke test shrank"


def test_the_release_reads_the_version_file_it_ships() -> None:
    """`ci.yml` runs on main, never on a tag, so the tag event must check itself."""

    text = _text(RELEASE)
    assert "version.txt" in text, "the release never reads the file it publishes"
    assert "tr -d" in text and "declared" in text, "version.txt is read but not compared"
    assert text.index("version.txt") < text.index("Create GitHub Release"), (
        "the version agreement is checked after the release is created"
    )


def test_the_compose_smoke_test_pins_the_deterministic_provider() -> None:
    """`.env.example` ships the real provider, so the smoke job has to say `synthetic`.

    The `compose` job boots the stack with `--env-file .env.example`, and `Smoke 1/5`
    asserts that syncing `DEMO-AAPL` inserted bars. Yahoo has no such symbol, so the
    stock template would sync 0 bars and fail the step; a job/shell value wins over the
    env file (ADR-077), which is why this job states the provider itself (ADR-172).
    """

    workflow = yaml.safe_load(_text(CI))
    job = (workflow.get("jobs") or {})["compose"]
    assert (job.get("env") or {}).get("MARKET_DATA_PROVIDER") == "synthetic", (
        "the compose smoke job inherits .env.example's yahoo_finance; Smoke 1/5 syncs DEMO-AAPL"
    )
    boots = [
        step
        for step in job.get("steps") or []
        if "--env-file .env.example" in str(step.get("run", ""))
    ]
    assert boots, "the compose job no longer boots from .env.example; this guard is moot"


def test_the_local_stack_script_can_fail() -> None:
    """The web half printed "vite did not come up" and still exited 0 (ADR-073 shape)."""

    text = _text(LOCAL_STACK)
    failure = text.index("vite did not come up")
    assert "exit 1" in text[failure:], "the vite failure path cannot fail the script"
    assert "START_LOCAL_STACK_FAILED" in text, "the failure has no machine-readable verdict"


def test_the_frontend_checks_do_not_read_a_variable_nobody_sets() -> None:
    """`$isUnc` was never assigned, so cleanup of the mirror never ran."""

    text = _text(FRONTEND_CHECKS)
    assert "$isUnc" not in text, "the dead variable is back; the branch it guards never runs"
    assert re.search(r"if\s*\(\s*\$needsMirror\s+-and\s+-not\s+\$KeepMirror\s*\)", text), (
        "the mirror cleanup no longer keys off the variable that decides mirroring"
    )


def test_the_release_notes_do_not_point_at_a_port_that_serves_nobody() -> None:
    """version.sh printed API docs on :8080, which is bound to localhost only."""

    notes = _text(VERSION_SH)
    readme = _text(README)
    in_notes = re.search(r":(\d+)/docs", notes)
    in_readme = re.search(r":(\d+)/docs", readme)
    assert in_notes, "the deploy notes no longer name the API docs URL"
    assert in_readme, "README no longer names the API docs URL"
    assert in_notes.group(1) == in_readme.group(1), (
        f"version.sh says :{in_notes.group(1)}/docs while README says :{in_readme.group(1)}/docs"
    )
