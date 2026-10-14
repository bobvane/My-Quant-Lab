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
import subprocess
import sys
import tempfile

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


def test_every_postgres_step_settles_its_verdict_from_the_junit_report() -> None:
    """A `postgres` step that only reads pytest's exit code goes green when it skips.

    The modules run against the CI service through `TEST_POSTGRES_URL`; unset, they
    are marked `postgres` and skip, and pytest exits 0 -- so the step reports success
    while proving nothing about PostgreSQL. ADR-212 (a column narrower than its value)
    and ADR-221 (a unique index) were both that shape. The B1 step already settled its
    verdict from `--junitxml` + `scripts/verify_b1_junit.py`; every `postgres` step now
    has to, and the expected count has to be stated rather than inferred.
    """

    workflow = yaml.safe_load(_text(CI))
    steps = (workflow.get("jobs") or {})["backend"]["steps"]
    postgres_steps = [
        step
        for step in steps
        if str(step.get("env", {}).get("TEST_POSTGRES_URL", "")).startswith("postgresql")
    ]
    assert len(postgres_steps) >= 4, (
        f"only {len(postgres_steps)} PostgreSQL steps found; the scan broke"
    )

    for step in postgres_steps:
        name = step.get("name", "<unnamed>")
        body = step.get("run", "")
        assert "--junitxml=" in body, (
            f"{name!r} never writes a JUnit report, so a skip is indistinguishable from a pass"
        )
        assert "verify_b1_junit.py" in body or "verify_pg_junit.py" in body, (
            f"{name!r} writes a JUnit report but never reads it"
        )
        assert "--expect" in body or "EXPECTED_TESTS=" in body, (
            f"{name!r} does not state the test count it requires"
        )


def test_every_postgres_step_expects_the_count_pytest_actually_collects() -> None:
    """`--expect N` is a claim about reality; collection is the reality, so read it.

    A `def test_` count is not the collected count: `@pytest.mark.parametrize` turns one
    function into several tests, so a hand-written expectation drifts from the module it
    guards. The expectation would then fail a legitimate green run -- or, worse, be
    quietly relaxed until it stopped meaning anything. These numbers are derived from
    `pytest --collect-only`, so a new parametrize case is caught here instead of in CI.
    """

    workflow = yaml.safe_load(_text(CI))
    steps = (workflow.get("jobs") or {})["backend"]["steps"]

    # Gather every (step, module, expected) first, then collect in ONE pytest process:
    # four separate collections cost a minute of importing `app` for no extra evidence.
    wants: list[tuple[str, str, int]] = []
    for step in steps:
        if not str(step.get("env", {}).get("TEST_POSTGRES_URL", "")).startswith("postgresql"):
            continue
        body = str(step.get("run", ""))
        module = re.search(r"(tests/test_[A-Za-z0-9_]+\.py)", body)
        assert module, f"{step.get('name')!r} names no test module; cannot derive a count"
        expected = re.search(r"--expect\s+(\d+)", body) or re.search(r"EXPECTED_TESTS=(\d+)", body)
        assert expected, f"{step.get('name')!r} states no expected count"
        wants.append((str(step.get("name")), module.group(1), int(expected.group(1))))

    assert len(wants) >= 4, f"only {len(wants)} PostgreSQL steps checked; the scan broke"

    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            *[module for _, module, _ in wants],
            "--collect-only",
            "-q",
            "--color=no",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT / "backend",
        capture_output=True,
        text=True,
    )
    assert collected.returncode == 0, (
        f"collecting the PostgreSQL modules failed:\n{collected.stdout}\n{collected.stderr}"
    )

    ids = [line for line in collected.stdout.splitlines() if "::" in line]
    for name, module, expected in wants:
        # `::` lines look like `tests/test_x.py::test_y`; normalise the separator win/posix.
        actual = len([line for line in ids if line.replace("\\", "/").startswith(module)])
        assert expected == actual, (
            f"{name!r} expects {expected} tests but {module} collects {actual}; the "
            "expectation would fail a green run or was relaxed to hide a skip"
        )


def test_every_postgres_step_proves_the_backend_from_the_pytest_header() -> None:
    """A `... on PostgreSQL` step must prove PostgreSQL, not just count green tests.

    `test_draft_answers.py` and `test_paper_idempotency.py` are backend-agnostic by
    design: with no `TEST_POSTGRES_URL` they run on in-memory SQLite and pass, so the
    JUnit counts are right and `skipped == 0` while PostgreSQL was never touched. The
    counts cannot tell the two runs apart; `conftest.pytest_report_header` can, because
    it prints `postgres tests: <host>` only when a database was actually offered.
    Every PostgreSQL step therefore has to read that line back out of its own log.
    """

    workflow = yaml.safe_load(_text(CI))
    steps = (workflow.get("jobs") or {})["backend"]["steps"]
    postgres_steps = [
        step
        for step in steps
        if str(step.get("env", {}).get("TEST_POSTGRES_URL", "")).startswith("postgresql")
    ]
    assert len(postgres_steps) >= 4, (
        f"only {len(postgres_steps)} PostgreSQL steps found; the scan broke"
    )

    for step in postgres_steps:
        name = step.get("name", "<unnamed>")
        body = str(step.get("run", ""))
        assert "verify_pg_dialect.py" in body, (
            f"{name!r} never proves the backend was PostgreSQL, so a run that quietly "
            "used SQLite passes while claiming to be a PostgreSQL test"
        )
        assert '"$LOG"' in body, f"{name!r} has no captured pytest log to read the header from"
        assert "-x" not in body.split("pytest")[1].split("\n")[0], (
            f"{name!r} passes a pytest flag that would change what the log contains"
        )


def test_the_dialect_verifier_refuses_a_run_that_never_offered_postgresql() -> None:
    """The verifier is the guard, so its own accepting and refusing must be pinned.

    Synthetic logs on purpose: this proves the rule, and cannot be mistaken for proof
    that a real PostgreSQL CI run happened.
    """

    verifier = REPO_ROOT / "backend" / "scripts" / "verify_pg_dialect.py"
    assert verifier.exists(), "scripts/verify_pg_dialect.py is missing"

    header_ok = "============================= test session starts =============================\n"
    cases = [
        # (label, log body, expected exit code)
        ("offered: real host", header_ok + "postgres tests: localhost:5433/quantlab_test\n", 0),
        (
            "not offered: the silent-SQLite run",
            header_ok + "postgres tests: not offered (TEST_POSTGRES_URL is unset)\n",
            1,
        ),
        ("absent: no header at all (e.g. -q)", header_ok + "47 passed in 10.55s\n", 1),
        ("empty log", "", 1),
        (
            "offered but also refused: contradiction refuses",
            "postgres tests: localhost:5433/quantlab_test\n"
            "postgres tests: not offered (TEST_POSTGRES_URL is unset)\n",
            1,
        ),
        (
            "indented mention is not the header",
            header_ok + "    postgres tests: localhost:5433/quantlab_test\n",
            1,
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        for label, body, expected in cases:
            log = pathlib.Path(tmp) / "probe.log"
            log.write_text(body, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(verifier), str(log)],
                cwd=REPO_ROOT / "backend",
                capture_output=True,
                text=True,
            )
            assert result.returncode == expected, (
                f"{label!r}: expected exit {expected}, got {result.returncode}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )

        missing = pathlib.Path(tmp) / "does-not-exist.log"
        result = subprocess.run(
            [sys.executable, str(verifier), str(missing)],
            cwd=REPO_ROOT / "backend",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, "a missing log must fail closed, not pass"


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
