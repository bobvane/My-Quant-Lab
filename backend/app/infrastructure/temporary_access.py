"""Temporary public access through a Cloudflare Quick Tunnel (ADR-125).

An operator sometimes needs a public URL for an hour: a UX review on a phone, a
remote demo, or a bug that only reproduces from outside the LAN. This module owns
the whole lifetime of that URL.

Three properties matter more than the feature itself:

* **Nothing is restored automatically.** The state lives in this process only, and
  a fresh process kills any ``cloudflared`` a previous one left behind. A public
  door must never reopen because a container restarted.
* **Nothing outlives its deadline.** Every tunnel records ``expires_at`` and is
  stopped by whichever comes first: the operator's request, the deadline, or the
  child process dying.
* **It only ever reaches the bundled web container.** The target is a compose
  service address (``http://quantlab-web:80``), never the API, the database, or any
  other service on the NAS. No Docker socket and no Cloudflare account are
  involved (ADR-024, ADR-125).

``cloudflared`` runs as a child of the API process, in its own session, with a
minimal environment: it inherits no secret from this deployment and needs none.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

# The five states the settings page knows how to draw. `stopping` is short-lived:
# it exists so a second request during teardown is refused instead of racing.
DISABLED = "disabled"
STARTING = "starting"
ACTIVE = "active"
STOPPING = "stopping"
ERROR = "error"

# A tunnel exists (or is about to) in these two states.
RUNNING_STATUSES = (STARTING, ACTIVE)

# Quick Tunnels print their address on stderr and change it on every start, so it
# is read from the stream rather than requested from an API.
PUBLIC_URL = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# The only trace left on disk, used to kill a leftover child after a restart.
PID_FILE = "/tmp/quantlab-cloudflared.pid"

# How much of the child's output is kept for diagnostics. Bounded on purpose: the
# logs of a long-running tunnel are not a place to grow.
_OUTPUT_TAIL = 8
_TERMINATE_GRACE_SECONDS = 5.0
_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class TunnelProcess(Protocol):
    """The slice of ``subprocess.Popen`` this module uses (tests supply their own)."""

    pid: int
    stdout: Iterable[str] | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class TemporaryAccessError(RuntimeError):
    """A refusal the API turns into an HTTP error with this status code."""

    def __init__(self, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _child_environment() -> dict[str, str]:
    """The smallest environment ``cloudflared`` can run with.

    The child must not inherit this deployment's secrets (``SECRET_KEY``,
    ``API_AUTH_TOKEN``, ``POSTGRES_PASSWORD``) — it needs none of them — and the
    process table must not become a copy of the container's environment. Only the
    names below are passed through, and only when they are actually set: an
    outbound proxy and a CA bundle are the two things a tunnel may legitimately
    need (ADR-125).
    """

    passthrough = (
        "PATH",
        "HOME",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    )
    env = {name: os.environ[name] for name in passthrough if os.environ.get(name)}
    env.setdefault("HOME", "/tmp")
    return env


def _process_command_line(pid: int) -> str:
    """The command line of ``pid``, or "" when it cannot be read.

    Linux only (``/proc``): the module ships inside the API container, and an
    unreadable pid simply means "not ours", which is the safe answer.
    """

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def _signal_process_group(pid: int, number: int) -> None:
    """Signal the session the child was spawned into.

    ``start_new_session=True`` gives the child its own session, so signalling the
    group reaches whatever ``cloudflared`` spawned in turn. Windows has no process
    groups; there the pid is signalled directly.
    """

    if hasattr(os, "killpg"):
        os.killpg(os.getpgid(pid), number)
        return
    os.kill(pid, number)  # pragma: no cover - Windows has no process groups


class TemporaryAccessManager:
    """Owns at most one Cloudflare Quick Tunnel for the life of this process.

    Every public method is safe to call at any time and answers with the same
    payload shape, so the API layer only maps refusals to status codes.
    """

    def __init__(
        self,
        *,
        spawn: Callable[[], TunnelProcess] | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = 1.0,
        pid_file: str = PID_FILE,
    ) -> None:
        # `spawn`, `clock` and `poll_seconds` are seams for tests: the shipped
        # manager is the module-level `manager` below, and a temporary tunnel must
        # never be started by a test suite (ADR-125).
        self._spawn = spawn or self._spawn_cloudflared
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._pid_file = pid_file

        self._lock = threading.RLock()
        self._process: TunnelProcess | None = None
        self._status = DISABLED
        self._url: str | None = None
        self._detail: str | None = None
        self._started_at: dt.datetime | None = None
        self._expires_at: dt.datetime | None = None
        self._deadline: float | None = None
        self._start_deadline: float | None = None
        self._monitor: threading.Thread | None = None
        self._stop_monitor = threading.Event()
        self._output: deque[str] = deque(maxlen=_OUTPUT_TAIL)

    # --- what the operator sees ------------------------------------------
    def status(self) -> dict[str, Any]:
        """The current state, after enforcing the deadline once more."""

        with self._lock:
            self._check_locked()
            return self._payload_locked()

    def start(self) -> dict[str, Any]:
        """Ask for a tunnel and return immediately.

        The address is not known yet when this returns: ``cloudflared`` takes a few
        seconds to register one, so the answer is ``starting`` and the caller polls
        ``status()``. Waiting here instead would hold an HTTP request open for as
        long as Cloudflare takes, and would say nothing while it happened.
        """

        if not settings.temporary_access_enabled:
            raise TemporaryAccessError(
                "temporary access is switched off in this deployment "
                "(TEMPORARY_ACCESS_ENABLED=false)",
                status_code=403,
            )
        with self._lock:
            self._check_locked()
            if self._status in RUNNING_STATUSES:
                raise TemporaryAccessError(
                    "a temporary tunnel is already open; close it before opening another",
                    status_code=409,
                )
            if self._status == STOPPING:
                raise TemporaryAccessError(
                    "the previous tunnel is still stopping; try again in a moment",
                    status_code=409,
                )

            self._clear_locked(STARTING)
            max_seconds = settings.temporary_access_max_duration_seconds
            self._started_at = dt.datetime.now(dt.UTC)
            self._expires_at = self._started_at + dt.timedelta(seconds=max_seconds)
            self._deadline = self._clock() + max_seconds
            self._start_deadline = self._clock() + settings.temporary_access_start_timeout_seconds
            logger.info(
                "Tunnel start requested (target=%s, max_duration=%ss)",
                settings.temporary_access_target_url,
                max_seconds,
            )
            try:
                process = self._spawn()
            except FileNotFoundError as exc:
                detail = (
                    f"cloudflared was not found ({exc}); the API image must ship the "
                    "binary (see docker/Dockerfile.backend)"
                )
                return self._fail_locked(detail, status_code=503, cause=exc)
            except OSError as exc:
                return self._fail_locked(
                    f"cloudflared could not be started: {exc}", status_code=503, cause=exc
                )

            self._process = process
            self._write_pid_file(process.pid)
            logger.info("Tunnel started (pid=%s)", process.pid)
            self._watch_locked(process)
            return self._payload_locked()

    def stop(self, *, reason: str = "requested") -> dict[str, Any]:
        """Close the tunnel and clear the state. Idempotent."""

        with self._lock:
            self._check_locked()
            if self._process is None:
                # Nothing of ours is running. Closing is a no-op that still answers
                # with a clean state, so a stale error cannot survive a close.
                self._clear_locked(DISABLED)
                return self._payload_locked()
            self._terminate_locked()
            logger.info("Tunnel stopped (%s)", reason)
            self._clear_locked(DISABLED)
            return self._payload_locked()

    def shutdown(self) -> None:
        """Stop the tunnel on the way out, whatever state it is in.

        Called from the application lifespan: a container that is going down must
        not leave a public address behind, and a failure here must not keep the API
        process from shutting down.
        """

        try:
            self.stop(reason="api shutdown")
        except Exception:  # pragma: no cover - shutdown never raises on purpose
            logger.exception("could not stop the temporary tunnel during shutdown")

    # --- health, deadline, leftovers -------------------------------------
    def check(self) -> dict[str, Any]:
        """Run the liveness and deadline rules by hand (same as ``status()``)."""

        return self.status()

    def check_locked(self) -> dict[str, Any]:
        """``check()`` for callers that already hold the lock."""

        self._check_locked()
        return self._payload_locked()

    def reap_orphans(self) -> list[int]:
        """Kill a ``cloudflared`` a previous incarnation of this container left.

        The state lives in memory, so a restarted API starts ``disabled`` — but the
        child process does not necessarily die with its parent, and a public address
        nobody can close is exactly what this feature must never leave behind. The
        pid file written at start is the only trace kept on disk, and it is trusted
        only while ``/proc`` still says that pid is a ``cloudflared`` (ADR-125).
        """

        path = Path(self._pid_file)
        try:
            recorded = path.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.warning("could not read the tunnel pid file %s: %s", path, exc)
            return []

        killed: list[int] = []
        for chunk in recorded:
            try:
                pid = int(chunk)
            except ValueError:
                continue
            if pid <= 0 or pid == os.getpid():
                continue
            if "cloudflared" not in _process_command_line(pid):
                logger.info("ignoring stale tunnel pid %s: it is not a cloudflared", pid)
                continue
            logger.warning(
                "found a leftover cloudflared from a previous process (pid=%s); stopping it", pid
            )
            try:
                _signal_process_group(pid, signal.SIGTERM)
            except OSError as exc:
                logger.warning("could not signal the leftover cloudflared (pid=%s): %s", pid, exc)
                continue
            time.sleep(0.5)
            if _process_command_line(pid):
                with contextlib.suppress(OSError):
                    _signal_process_group(pid, _KILL)
            killed.append(pid)
        self._remove_pid_file()
        return killed

    # --- internals -------------------------------------------------------
    def _fail_locked(
        self, detail: str, *, status_code: int, cause: Exception | None = None
    ) -> dict[str, Any]:
        """Record a start that never got off the ground, and say why."""

        logger.error("Tunnel start failed: %s", detail)
        self._clear_locked(ERROR, detail)
        raise TemporaryAccessError(detail, status_code=status_code) from cause

    def _check_locked(self) -> None:
        """The two rules that end a tunnel without anyone asking."""

        process = self._process
        if process is None:
            return

        code = process.poll()
        if code is not None:
            if self._status == STOPPING:
                self._clear_locked(DISABLED)
                return
            logger.warning("Tunnel process exited unexpectedly (exit code %s)", code)
            self._clear_locked(ERROR, f"cloudflared exited unexpectedly (exit code {code})")
            return

        now = self._clock()
        if (
            self._status == STARTING
            and self._start_deadline is not None
            and now >= self._start_deadline
        ):
            detail = (
                "cloudflared did not report a public address within "
                f"{settings.temporary_access_start_timeout_seconds}s"
            )
            logger.error("Tunnel start failed: %s", detail)
            if self._output:
                logger.error("last cloudflared output: %s", self._output[-1])
            self._terminate_locked()
            self._clear_locked(ERROR, detail)
            return

        if (
            self._status in RUNNING_STATUSES
            and self._deadline is not None
            and now >= self._deadline
        ):
            logger.info("Tunnel expired after %ss", settings.temporary_access_max_duration_seconds)
            self._terminate_locked()
            logger.info("Tunnel stopped (expired)")
            self._clear_locked(
                DISABLED,
                "the tunnel reached its time limit and was closed automatically",
            )

    def _terminate_locked(self) -> None:
        """Stop the child, escalating to SIGKILL if it ignores SIGTERM."""

        process = self._process
        if process is None:
            return
        pid = process.pid
        self._status = STOPPING
        try:
            _signal_process_group(pid, signal.SIGTERM)
        except OSError:
            with contextlib.suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            logger.warning("cloudflared (pid=%s) ignored SIGTERM; killing it", pid)
        try:
            _signal_process_group(pid, _KILL)
        except OSError:  # pragma: no cover - it died between the two signals
            with contextlib.suppress(OSError):
                process.kill()
        try:
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
            logger.error("cloudflared (pid=%s) did not exit after SIGKILL", pid)

    def _clear_locked(self, status: str, detail: str | None = None) -> None:
        """Forget the tunnel: no process, no address, no deadline."""

        self._stop_monitor.set()
        self._stop_monitor = threading.Event()
        self._process = None
        self._url = None
        self._detail = detail
        self._started_at = None
        self._expires_at = None
        self._deadline = None
        self._start_deadline = None
        self._status = status
        self._remove_pid_file()

    def _watch_locked(self, process: TunnelProcess) -> None:
        """Start the two threads that watch a live tunnel.

        The reader turns cloudflared's own output into the public address; the
        monitor enforces liveness and the deadline even when nothing polls the API.
        Both are daemons, both end when the tunnel does, and neither is required for
        correctness: ``status()`` runs the same rules on every call.
        """

        stop_event = self._stop_monitor
        threading.Thread(
            target=self._read_output,
            args=(process,),
            name="cloudflared-output",
            daemon=True,
        ).start()
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            args=(process, stop_event),
            name="cloudflared-monitor",
            daemon=True,
        )
        self._monitor.start()

    def _monitor_loop(self, process: TunnelProcess, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._poll_seconds):
            with self._lock:
                if self._process is not process:
                    return
                self._check_locked()

    def _read_output(self, process: TunnelProcess) -> None:
        stream = process.stdout
        if stream is None:  # pragma: no cover - the shipped spawner always pipes
            return
        try:
            for line in stream:
                text = line.rstrip("\n")
                self._output.append(text)
                found = PUBLIC_URL.search(text)
                if found is None:
                    continue
                with self._lock:
                    if self._process is not process or self._status not in RUNNING_STATUSES:
                        return
                    self._url = found.group(0)
                    self._detail = None
                    self._status = ACTIVE
                    logger.info("Tunnel URL obtained: %s", self._url)
        except (OSError, ValueError):  # pragma: no cover - the stream closed under us
            return

    def _payload_locked(self) -> dict[str, Any]:
        remaining: int | None = None
        if self._deadline is not None and self._status in RUNNING_STATUSES:
            remaining = max(0, int(round(self._deadline - self._clock())))
        return {
            "status": self._status,
            "url": self._url,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "expires_at": self._expires_at.isoformat() if self._expires_at else None,
            "remaining_seconds": remaining,
            "max_duration_seconds": settings.temporary_access_max_duration_seconds,
            "enabled": settings.temporary_access_enabled,
            # Answering "which service is this exposing?" is part of the security
            # story: the address below is the bundled web container and nothing else.
            "target_url": settings.temporary_access_target_url,
            "detail": self._detail,
        }

    def _spawn_cloudflared(self) -> TunnelProcess:
        command = [
            settings.temporary_access_cloudflared_bin,
            "tunnel",
            "--no-autoupdate",
            "--url",
            settings.temporary_access_target_url,
        ]
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_child_environment(),
            start_new_session=True,
        )

    def _write_pid_file(self, pid: int) -> None:
        try:
            Path(self._pid_file).write_text(f"{pid}\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write the tunnel pid file %s: %s", self._pid_file, exc)

    def _remove_pid_file(self) -> None:
        try:
            Path(self._pid_file).unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("could not remove the tunnel pid file %s: %s", self._pid_file, exc)


manager = TemporaryAccessManager()
