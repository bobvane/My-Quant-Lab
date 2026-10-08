"""A database that will never accept us must not look like a slow one.

`docker/entrypoint.sh` asked PostgreSQL a real question, then threw the answer
away (`>/dev/null 2>&1`): a wrong password, a database that was never created or
a host name that does not resolve all looked identical -- thirty lines of
"waiting for database (N/30)" for 60 s, followed by "migrations failed;
refusing to start". The wall clock cannot change any of those answers, and that
message named the wrong cause (ADR-080).

These guards hold the fix in place: the wait keeps the reason PostgreSQL gave,
prints it (once, and again when it changes), and fails immediately when the
answer is one that waiting cannot change -- while the genuinely transient cases
(server still starting, connection refused) keep waiting and still let alembic
report the real error. The behaviour tests drive the real script with a stubbed
interpreter, because the point is what the container prints and what it exits
with, not what the script says about itself.

The four processes live in one container now, so there are two roles left:
`app` (the whole deployment) and `migrate` (an operator's manual step). That
changes who can report a dead database: the App's wait is fatal because a
container that came up without PostgreSQL would report "healthy" for four
children that can serve nothing, and no second container is left to name the
reason (ADR-099); a hand-run `migrate` keeps the soft wait, because alembic's own
error names the host, port and database it tried.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required to exercise the script")

ROLES = ("app", "migrate")

# Answers that no amount of waiting can change, and answers that a slow start
# can produce. Kept apart because one list must fail fast and the other must not.
# Nothing in these samples may contain a double quote: they are handed to a shell
# inside a double-quoted word.
_DRIVER_FAILURE = "not ready: (psycopg.OperationalError) connection failed: FATAL:  "
PERMANENT_ANSWERS = (
    _DRIVER_FAILURE + "password authentication failed for user quantlab",
    _DRIVER_FAILURE + "database quantlab does not exist",
    _DRIVER_FAILURE + "NoSuchModuleError: Can't load plugin: psycopg3",
    "could not translate host name db to address",
)
TRANSIENT_ANSWERS = (
    "connection refused",
    "the database system is starting up",
    "the database system is in recovery mode",
    "server closed the connection unexpectedly",
    "timeout expired",
)

# A python that answers the entrypoint's three questions: the settings probe, the
# database probe (per PROBE_MODE) and `import app`. It returns instead of exiting,
# because an `exit` inside a shell function would end the driver instead of
# behaving like an interpreter process that failed.
DRIVER = """#!/bin/bash
set -u

probe_log="${PROBE_LOG:-/dev/null}"
alembic_log="${ALEMBIC_LOG:-/dev/null}"
: > "$probe_log"
: > "$alembic_log"

PREFIX='not ready: (psycopg.OperationalError) connection failed: FATAL: '
PASSWORD='password authentication failed for user "quantlab"'
MISSING='database "quantlab" does not exist'
# An exported function travels to the child bash as text; the variables it reads do
# not, unless they are exported too -- otherwise the stub answers with an empty
# reason and the entrypoint waits the full budget for nothing.
export PREFIX PASSWORD MISSING

python() {
    case "$*" in
        *"SELECT 1"*)
            printf 'probe\\n' >> "$probe_log"
            n=$(wc -l < "$probe_log" | tr -d ' ')
            case "${PROBE_MODE:-refused}" in
                ready-on-second)
                    if [ "$n" -ge 2 ]; then return 0; fi
                    echo "connection refused" >&2
                    return 1
                    ;;
                wrong-password)
                    echo "${PREFIX}${PASSWORD}" >&2
                    return 1
                    ;;
                missing-database)
                    echo "${PREFIX}${MISSING}" >&2
                    return 1
                    ;;
                *)
                    echo "connection refused" >&2
                    return 1
                    ;;
            esac
            ;;
        *"settings.environment"*) echo "${STUB_ENVIRONMENT:-test}"; return 0 ;;
        *"import app"*) echo "9.9.9"; return 0 ;;
        -V) echo "Python 3.13.0"; return 0 ;;
    esac
    return 0
}

alembic() {
    echo "alembic $*" >> "$alembic_log"
    echo "stub alembic $*"
    return "${ALEMBIC_RC:-0}"
}

export -f python alembic
export probe_log alembic_log
entrypoint="${1:?usage: driver.sh <entrypoint.sh>}"
set +e
bash "$entrypoint"
rc=$?
set -e
probes=$(wc -l < "$probe_log" | tr -d ' ')
alembic_calls=$(wc -l < "$alembic_log" | tr -d ' ')
echo "=== entrypoint rc=$rc probes=$probes alembic_calls=$alembic_calls ==="
"""

SUMMARY = re.compile(r"=== entrypoint rc=(\d+) probes=(\d+) alembic_calls=(\d+) ===")


def _text() -> str:
    return ENTRYPOINT.read_text(encoding="utf-8")


def _role_branch(text: str, role: str) -> str:
    """Return the body of one `case "$ROLE"` branch."""

    start = text.index(f"\n    {role})\n")
    end = text.index("\n        ;;", start)
    return text[start:end]


def _main_app(text: str) -> str:
    """Return the body of `main_app()`, where the App role's startup order lives.

    The `app` branch itself says only `main_app`, so the interesting assertions
    are one level in (ADR-099).
    """

    return text[text.index("main_app() {") : text.index('case "$ROLE" in')]


def _function(text: str, name: str) -> str:
    """Return one shell function, header to closing brace.

    Slicing between two function headers would swallow whatever comments sit
    between them, and those comments change; this stops at the function's own end.
    """

    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start)
    return text[start : end + 2]


def _posix(path: pathlib.Path) -> str:
    """A path a POSIX shell can use in a redirection.

    Git Bash converts command arguments, but a redirection target is passed to
    open() as written, so `C:/...` fails there and a driver that logs into such a
    path dies with "No such file or directory" instead of testing anything.
    """

    raw = str(path)
    if os.name != "nt":
        return raw
    if raw.startswith("\\\\"):
        return raw.replace("\\", "/")
    drive, rest = os.path.splitdrive(raw)
    return "/" + drive[0].lower() + rest.replace("\\", "/")


def _functions() -> str:
    """Everything before the role dispatch, so the helpers can be called alone."""

    text = _text()
    return text[: text.index('case "$ROLE" in')]


def _run_bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet of the entrypoint through stdin.

    Deliberately not `bash -c`: the scripts here are the concatenated helpers of a
    launcher, and this platform's bash silently mangles a long `-c` argument (the
    same text through a file or stdin runs correctly). `-s` is the same shell
    executing the same text, which is all these guards need.
    """

    assert BASH is not None
    return subprocess.run(
        [BASH, "-s"],
        input=script,
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=180,
    )


def _drive(tmp_path: pathlib.Path, mode: str, **env: str) -> tuple[str, int, int, int]:
    """Run the real entrypoint as `migrate`, with stubbed interpreters."""

    driver = tmp_path / "driver.sh"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    probe_log = tmp_path / f"probes-{mode}"
    alembic_log = tmp_path / f"alembic-{mode}"
    assert BASH is not None
    finished = subprocess.run(
        [BASH, _posix(driver), "docker/entrypoint.sh"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PROBE_MODE": mode,
            "PROBE_LOG": _posix(probe_log),
            "ALEMBIC_LOG": _posix(alembic_log),
            "APP_ROLE": "migrate",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    match = SUMMARY.search(finished.stdout)
    assert match, f"the driver did not report a summary:\n{finished.stdout}\n{finished.stderr}"
    rc, probes, alembic_calls = (int(value) for value in match.groups())
    return finished.stdout, rc, probes, alembic_calls


# --- what the script says about itself -------------------------------------


def test_the_wait_keeps_the_answer_postgresql_gave() -> None:
    """The bug was a redirect: the reason was produced and then discarded.

    Scoped to the probe on purpose: the launcher's teardown helpers do write
    `2>/dev/null`, but that suppresses `kill: no such process`, not a diagnostic
    a human needs.
    """

    text = _text()
    probe = text[text.index("database_probe() {") : text.index("database_error_is_permanent() {")]
    assert ">/dev/null" not in probe, "the probe throws its diagnostics away"
    assert "if reason=$(database_probe 2>&1); then" in text, (
        "the caller no longer keeps the answer the probe produced"
    )


def test_the_wait_names_the_reason_it_is_waiting() -> None:
    text = _text()
    assert 'log "database not ready yet: $reason"' in text
    assert 'last_reason="$reason"' in text, "the same reason must not be reprinted forever"


def test_a_permanent_answer_is_not_waited_for() -> None:
    text = _text()
    assert "database_error_is_permanent() {" in text
    body = text[text.index("database_error_is_permanent() {") : text.index("wait_for_db() {")]
    for reason in ("password authentication failed", "does not exist", "NoSuchModuleError"):
        assert f'*"{reason}"*' in body, f"a {reason!r} answer would still be waited for"
    wait = _function(text, "wait_for_db")
    assert 'if database_error_is_permanent "$reason"; then' in wait
    assert "giving up now instead of retrying" in wait
    assert wait.index("database_error_is_permanent") < wait.index('log "waiting for database')
    # The message an operator reads while staring at a dead stack must name the
    # variable this deployment actually reads. It used to say `DB_HOST`, which no
    # code in the repository consumes (ADR-102).
    assert "check POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB and POSTGRES_HOST" in wait
    assert "DB_HOST" not in wait, "the diagnostics name a variable nothing reads"


def test_the_budget_is_a_decision_not_a_constant() -> None:
    text = _text()
    assert 'attempts="${DB_WAIT_ATTEMPTS:-30}"' in text
    assert 'interval="${DB_WAIT_INTERVAL:-2}"' in text
    assert "defaults 30 x 2 s = 60 s" in text, (
        "the 60 s healthcheck budget claim belongs with the numbers"
    )


def test_the_deployment_passes_the_budget_through() -> None:
    """A budget the deployment cannot set is not a decision the operator can make."""

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    knobs = (("attempts", "DB_WAIT_ATTEMPTS", "30"), ("interval", "DB_WAIT_INTERVAL", "2"))
    for local, name, default in knobs:
        assert f'{local}="${{{name}:-{default}}}"' in _text(), f"{name} lost its default"
        assert f"{name}: ${{{name}:-{default}}}" in compose, f"{name} never reaches the container"
        assert name in example, f"{name} is not documented where operators look"


def test_the_transient_path_still_lets_alembic_report_the_error() -> None:
    text = _text()
    wait = _function(text, "wait_for_db")
    assert "continuing so alembic reports the real error" in wait
    assert wait.rstrip().endswith("return 0\n}"), "a slow database must not stop the container here"


@pytest.mark.parametrize("role", ROLES)
def test_every_role_refuses_to_start_when_the_database_is_refused(role: str) -> None:
    text = _text()
    body = _main_app(text) if role == "app" else _role_branch(text, role)
    assert "wait_for_db" in body, f"{role} would carry on without a database"
    assert "|| exit 1" in body, f"{role} ignores the wait's verdict"


def test_the_app_makes_a_dead_database_fatal_and_the_manual_step_does_not() -> None:
    """The App is the deployment; `migrate` is an operator watching a terminal.

    Four roles used to be four containers, and the ones without migrations had to
    grow `--required`: with the soft exit they stayed up and failed every task
    while compose called them healthy (ADR-099). Now the whole deployment is the
    one container, so the App's own wait is the fatal one -- and a hand-run
    `migrate` deliberately is not.
    """

    text = _text()
    assert "wait_for_db --required || exit 1" in _main_app(text), (
        "the App may start four children against a database that never answered"
    )
    migrate = _role_branch(text, "migrate")
    assert "wait_for_db || exit 1" in migrate
    assert "--required" not in migrate, (
        "migrate runs migrations; alembic has a better message than the wait"
    )
    wait = _function(text, "wait_for_db")
    assert 'if [ "${1:-}" = "--required" ]; then' in wait
    assert "no second container left to name the reason" in text, (
        "the reason the App's wait is fatal is no longer written down where the "
        "next reader will find it"
    )


@needs_bash
def test_an_app_with_no_database_never_starts_its_children(tmp_path: pathlib.Path) -> None:
    output, rc, probes, alembic_calls = _drive(
        tmp_path, "refused", APP_ROLE="app", DB_WAIT_ATTEMPTS="2", DB_WAIT_INTERVAL="0"
    )
    assert rc == 1, "an App that cannot reach its database must not report success"
    assert probes == 2, "it must still use the whole budget before giving up"
    assert alembic_calls == 0
    assert "database not ready after" in output
    assert "continuing so alembic reports the real error" not in output
    for name in ("beat", "worker", "api", "nginx"):
        assert f"starting {name}:" not in output, f"the {name} child started anyway"
    assert "all four processes are running" not in output


# --- what the container actually prints and exits with ---------------------


@needs_bash
def test_a_refused_password_fails_on_the_first_answer(tmp_path: pathlib.Path) -> None:
    output, rc, probes, alembic_calls = _drive(tmp_path, "wrong-password")
    assert rc == 1
    assert probes == 1, "a password the server will never accept is not worth a second attempt"
    assert alembic_calls == 0, "migrations must not run against a database we cannot reach"
    assert "password authentication failed" in output, "the reason PostgreSQL gave is missing"
    assert "waiting for database" not in output, "the clock was still consulted"
    assert "POSTGRES_PASSWORD" in output, "the message must point at what to fix"


@needs_bash
def test_a_missing_database_fails_on_the_first_answer(tmp_path: pathlib.Path) -> None:
    output, rc, probes, alembic_calls = _drive(tmp_path, "missing-database")
    assert rc == 1
    assert probes == 1
    assert alembic_calls == 0
    assert 'database "quantlab" does not exist' in output


@needs_bash
def test_a_connection_that_may_come_up_still_waits(tmp_path: pathlib.Path) -> None:
    output, rc, probes, alembic_calls = _drive(
        tmp_path, "refused", DB_WAIT_ATTEMPTS="2", DB_WAIT_INTERVAL="0"
    )
    assert rc == 0
    assert probes == 2, "a refused connection deserves the full budget"
    assert alembic_calls == 1, "the transient path must still reach migrations"
    assert output.count("database not ready yet:") == 1, "the same reason must be printed once"
    assert output.count("waiting for database") == 2
    assert "continuing so alembic reports the real error" in output


@needs_bash
def test_a_database_that_comes_up_is_reported_ready(tmp_path: pathlib.Path) -> None:
    output, rc, probes, alembic_calls = _drive(
        tmp_path, "ready-on-second", DB_WAIT_ATTEMPTS="5", DB_WAIT_INTERVAL="0"
    )
    assert rc == 0
    assert probes == 2
    assert alembic_calls == 1
    assert "database is ready" in output


@needs_bash
@pytest.mark.parametrize("answer", TRANSIENT_ANSWERS)
def test_a_transient_answer_is_never_treated_as_permanent(answer: str) -> None:
    """A false fail-fast would turn a slow start into a crash loop."""

    finished = _run_bash(
        "\n".join(
            [
                "set -u",
                _functions(),
                f'database_error_is_permanent "{answer}" && {{ echo permanent; exit 0; }}',
                "echo transient",
            ]
        )
    )
    assert "transient" in finished.stdout, f"{answer!r} would stop the container immediately"


@needs_bash
@pytest.mark.parametrize("answer", PERMANENT_ANSWERS)
def test_a_permanent_answer_is_recognised_as_permanent(answer: str) -> None:
    finished = _run_bash(
        "\n".join(
            [
                "set -u",
                _functions(),
                f'database_error_is_permanent "{answer}" && {{ echo permanent; exit 0; }}',
                "echo transient",
            ]
        )
    )
    assert "permanent" in finished.stdout, f"{answer!r} would be waited for"
