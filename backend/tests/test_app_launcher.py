"""The launcher's behaviour: four children, one exit code, no supervisor (ADR-190).

`backend/tests/test_app_container.py` reads the shipped files; this file runs the
real `docker/entrypoint.sh`. Docker is not available on this workstation, and the
two properties that keep the merge safe are exactly the ones a text guard cannot
prove:

* all four children are started, in the documented order, exactly once;
* when one of them exits, the launcher tears the others down and exits non-zero so
  that compose's `restart: unless-stopped` starts a complete App again — no respawn
  loop, no half-deployment pretending to be healthy;
* a stop signal is handled by the launcher (children forwarded, bounded wait, exit
  0) instead of leaving PID 1 waiting for a second signal.

The script under test is a copy with four path constants pointed into a temp
directory (the three `/app/beat` + `/tmp` + `/etc/nginx` locations it would own
inside the image); every line of control flow is the shipped one. `celery`,
`uvicorn`, `nginx`, `python` and `alembic` are shell functions, so the test can see
who was started, with what, and who was asked to stop.

Signals are sent from inside the driver's own shell rather than from Python:
Windows' `terminate()` is not SIGTERM, and a guard that never delivers a signal
would pass whether or not the trap works.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required to run the launcher")

CELERY_APP = "app.workers.celery_app.celery_app"

DRIVER = r"""#!/bin/bash
set -u

markers="${MARKERS:?}"
entrypoint="${1:?usage: driver.sh <entrypoint.sh>}"
mkdir -p "$markers"
export markers

# --- the four children -----------------------------------------------------
# Each one records that it started (appending, so a respawn would be visible) and
# that it was asked to stop. An exported function travels to the child bash as
# text; the variables it reads travel only because they are exported too.
_child() {
    local name="$1"
    echo "started" >> "$markers/$name.started"
    trap 'echo term >> "$markers/'"$name"'.term"; exit 0' TERM
    if [ "$name" = "nginx" ]; then
        # nginx runs only after the config was rendered: if the file is not there
        # yet, that is not the order the launcher uses.
        cp "$STUB_RENDERED" "$markers/rendered" 2>/dev/null || true
    fi
    if [ "${DEATH_NAME:-}" = "$name" ]; then
        sleep "${DEATH_AFTER:-4}"
        echo died >> "$markers/$name.died"
        exit "${DEATH_RC:-3}"
    fi
    while :; do sleep 0.2; done
}

celery() {
    case "$*" in
        *beat*) _child beat ;;
        *) _child worker ;;
    esac
}
uvicorn() { _child api; }
nginx() { _child nginx; }

# --- the interpreter the launcher asks its questions -----------------------
python() {
    if [ "${1:-}" = "-V" ]; then echo "Python 3.12.0"; return 0; fi
    if [ "${1:-}" = "-c" ]; then
        case "${2:-}" in
            *"settings.environment"*) echo "test"; return 0 ;;
            *"SELECT 1"*) echo probe >> "$markers/probes"; return 0 ;;
            *"import app"*) echo "9.9.9"; return 0 ;;
        esac
        return 0
    fi
    # No arguments: the launcher fed a program on stdin. The nginx render is the
    # only one, and this is where its one substitution actually happens.
    program=$(cat)
    case "$program" in
        *AUTH_LINE*) ;;
        *) return 0 ;;
    esac
    : > "$STUB_RENDERED"
    while IFS= read -r line; do
        printf '%s\n' "${line//\$\{AUTH_LINE\}/$AUTH_LINE}" >> "$STUB_RENDERED"
    done < "$STUB_TEMPLATE"
    return 0
}

alembic() {
    echo "alembic $*" >> "$markers/alembic"
    return 0
}

export -f _child celery uvicorn nginx python alembic

# --- run the launcher and wait for it the way Docker would ------------------
bash "$entrypoint" &
pid=$!

waited=0
while [ "$waited" -lt 100 ]; do
    if [ -f "$markers/beat.started" ] && [ -f "$markers/worker.started" ] \
        && [ -f "$markers/api.started" ] && [ -f "$markers/nginx.started" ]; then
        break
    fi
    sleep 0.1
    waited=$((waited + 1))
done

if [ "${STOP_MODE:-term}" = "term" ]; then
    kill -TERM "$pid" 2>/dev/null || true
fi

set +e
wait "$pid"
rc=$?
set -e

alembic_calls=$(grep -c . "$markers/alembic" 2>/dev/null || echo 0)
probes=$(grep -c . "$markers/probes" 2>/dev/null || echo 0)
echo "=== launcher rc=$rc alembic=$alembic_calls probes=$probes ==="
for name in beat worker api nginx; do
    starts=$(grep -c . "$markers/$name.started" 2>/dev/null || echo 0)
    terms=$(grep -c . "$markers/$name.term" 2>/dev/null || echo 0)
    echo "child $name starts=$starts terms=$terms"
done
if [ -f "$markers/rendered" ]; then
    echo "rendered-begin"
    cat "$markers/rendered"
    echo "rendered-end"
fi
echo "=== end ==="
"""

SUMMARY = re.compile(r"=== launcher rc=(-?\d+) alembic=(\d+) probes=(\d+) ===")
CHILD = re.compile(r"^child (beat|worker|api|nginx) starts=(\d+) terms=(\d+)$", re.MULTILINE)
RENDERED = re.compile(r"^rendered-begin\n(.*?)^rendered-end$", re.MULTILINE | re.DOTALL)

CHILDREN = ("beat", "worker", "api", "nginx")
TEMPLATE = "server {\n    ${AUTH_LINE}\n}\n"


def _posix(path: pathlib.Path) -> str:
    """A path this platform's bash can use.

    The entrypoint is a shell file, so the temp paths written into it have to be
    the shell's spelling of them, not Python's.
    """

    raw = str(path)
    if os.name != "nt":
        return raw
    if raw.startswith("\\\\"):
        return raw.replace("\\", "/")
    drive, rest = os.path.splitdrive(raw)
    return "/" + drive[0].lower() + rest.replace("\\", "/")


def _launcher(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """The shipped launcher with only its four well-known paths retargeted."""

    text = ENTRYPOINT.read_text(encoding="utf-8")
    template = tmp_path / "nginx.template"
    rendered = tmp_path / "nginx.rendered"
    for old, new in (
        (
            'BEAT_SCHEDULE="/app/beat/celerybeat-schedule"',
            f'BEAT_SCHEDULE="{_posix(tmp_path / "celerybeat-schedule")}"',
        ),
        (
            'BEAT_PIDFILE="/app/beat/celerybeat.pid"',
            f'BEAT_PIDFILE="{_posix(tmp_path / "celerybeat.pid")}"',
        ),
        (
            'NGINX_RENDERED_CONF="/tmp/quantlab-nginx.conf"',
            f'NGINX_RENDERED_CONF="{_posix(rendered)}"',
        ),
        (
            'NGINX_TEMPLATE="/etc/nginx/quantlab-app.conf.template"',
            f'NGINX_TEMPLATE="{_posix(template)}"',
        ),
    ):
        assert old in text, f"the launcher no longer declares {old}"
        text = text.replace(old, new)

    copy = tmp_path / "entrypoint.sh"
    copy.write_text(text, encoding="utf-8", newline="\n")
    template.write_text(TEMPLATE, encoding="utf-8", newline="\n")
    return copy, template, rendered


def _drive(
    tmp_path: pathlib.Path,
    *,
    stop: str = "term",
    death_name: str | None = None,
    token: str | None = None,
) -> tuple[str, int, dict[str, tuple[int, int]], dict[str, int], str | None]:
    """Run the launcher with stub children and report what it did."""

    # A fresh directory per run: a marker left by an earlier run would let the
    # driver think the children were up and stop the launcher before it had even
    # installed its trap.
    work = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
    copy, template, rendered = _launcher(work)
    markers = work / "markers"
    driver = work / "driver.sh"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")

    env = {
        **os.environ,
        "APP_ROLE": "app",
        "MARKERS": _posix(markers),
        "STUB_TEMPLATE": _posix(template),
        "STUB_RENDERED": _posix(rendered),
        "STOP_MODE": stop,
        # A shorter bound than the deployment's, so a child that ignores SIGTERM
        # fails the guard instead of holding the suite open for 30 s.
        "SHUTDOWN_GRACE_SECONDS": "10",
        "DB_WAIT_ATTEMPTS": "2",
        "DB_WAIT_INTERVAL": "1",
    }
    if death_name is not None:
        env["DEATH_NAME"] = death_name
        env["DEATH_AFTER"] = "4"
        env["DEATH_RC"] = "3"
    if token is not None:
        env["API_AUTH_TOKEN"] = token

    assert BASH is not None
    finished = subprocess.run(
        [BASH, _posix(driver), _posix(copy)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    stdout = finished.stdout
    match = SUMMARY.search(stdout)
    assert match, f"the driver reported no summary:\n{stdout}\n{finished.stderr}"
    rc, alembic_calls, probes = (int(value) for value in match.groups())

    counts = dict.fromkeys(CHILDREN, (0, 0))
    for name, starts, terms in CHILD.findall(stdout):
        counts[name] = (int(starts), int(terms))
    rendered_match = RENDERED.search(stdout)
    return (
        stdout,
        rc,
        counts,
        {"alembic": alembic_calls, "probes": probes},
        rendered_match.group(1) if rendered_match else None,
    )


def _starts(stdout: str) -> list[str]:
    return re.findall(r"^\[entrypoint\] starting (beat|worker|api|nginx):", stdout, re.MULTILINE)


@needs_bash
def test_the_launcher_starts_four_children_and_stops_cleanly(tmp_path: pathlib.Path) -> None:
    """An operator's `docker stop` is a clean stop, and it reaches every child."""

    stdout, rc, counts, calls, _ = _drive(tmp_path)

    assert _starts(stdout) == list(CHILDREN), "the children are not started once in order"
    assert "all four processes are running: beat worker api nginx" in stdout
    assert counts == dict.fromkeys(CHILDREN, (1, 1)), (
        f"a child was started or stopped a different number of times: {counts}"
    )
    assert calls == {"alembic": 1, "probes": 1}, (
        f"the launcher migrated or probed a different number of times: {calls}"
    )

    assert "received a stop signal; terminating beat worker api nginx" in stdout
    assert "[entrypoint] stopped" in stdout
    assert "SIGKILL" not in stdout, "the children had to be killed; SIGTERM was not enough"
    assert rc == 0, f"a clean stop exited {rc}"


@needs_bash
def test_a_dead_child_stops_the_whole_app(tmp_path: pathlib.Path) -> None:
    """One child gone means the container is incomplete; compose restarts a whole App."""

    stdout, rc, counts, _, _ = _drive(tmp_path, stop="none", death_name="api")

    assert _starts(stdout) == list(CHILDREN)
    assert "a critical process exited (status 3); stopping the App" in stdout
    assert rc == 1, "a dead child must not look like a clean stop"

    # The dead one is not restarted, and the survivors were asked to stop rather
    # than abandoned (which would leave orphans under `init: true`).
    assert counts["api"] == (1, 0), f"the dead child was respawned: {counts}"
    for name in ("beat", "worker", "nginx"):
        assert counts[name] == (1, 1), f"{name} was not torn down: {counts}"
    assert "[entrypoint] stopped" not in stdout, "a crash reported itself as a clean stop"


@needs_bash
def test_the_rendered_config_is_what_nginx_is_given(tmp_path: pathlib.Path) -> None:
    """The token is injected into the config, and only there (ADR-103, ADR-190)."""

    _, rc, _, _, rendered = _drive(tmp_path)
    assert rc == 0
    assert rendered is not None, "nginx was started before the config existed"
    assert "${AUTH_LINE}" not in rendered, "the placeholder reached nginx"
    assert "Authorization" not in rendered, "a token appeared with no token configured"

    token = "tok-0123456789abcdef"
    stdout, _, _, _, rendered = _drive(tmp_path, token=token)
    assert "rendered the nginx configuration (bearer token injected)" in stdout
    assert rendered is not None
    assert f'proxy_set_header Authorization "Bearer {token}";' in rendered
    assert "${AUTH_LINE}" not in rendered

    stdout, _, _, _, rendered = _drive(tmp_path)
    assert "rendered the nginx configuration (no bearer token configured)" in stdout
    assert rendered is not None
    assert "proxy_set_header Authorization" not in rendered
