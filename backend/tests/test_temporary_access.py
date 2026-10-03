"""Temporary remote access: the tunnel that must never open by itself (ADR-125).

Every test here replaces the process and the clock. A suite that started a real
``cloudflared`` would open a real public address, depend on the public internet,
and leave something behind when it failed — so the tunnel is exercised through the
two seams the manager exists to provide, and the signals are recorded rather than
sent (a fake pid must never be signalled).
"""

from __future__ import annotations

import signal
import time

import pytest
from sqlalchemy import select

from app.api.routers import temporary_access as temporary_access_router
from app.core.config import settings
from app.domain.models import AuditLog
from app.infrastructure import temporary_access

URL = "https://random-words-here.trycloudflare.com"
URL_LINE = f"2026-10-03T09:00:00Z INF |  {URL}  |\n"
START_PATH = "/api/v1/settings/temporary-access/start"
STOP_PATH = "/api/v1/settings/temporary-access/stop"
STATE_PATH = "/api/v1/settings/temporary-access"


class FakeClock:
    """A clock the test moves by hand; the tunnel's deadlines are minutes long."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    """A ``cloudflared`` that only exists inside this test."""

    def __init__(
        self, *, pid: int = 4242, lines: tuple[str, ...] = (), exit_code: int | None = None
    ) -> None:
        self.pid = pid
        self.stdout = list(lines)
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        # A stopped child reports a code; a live one is only "waited for" after a
        # signal, which is what the manager does.
        if self.exit_code is None:
            self.exit_code = 0
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = 0


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def signals(monkeypatch) -> list[tuple[int, int]]:
    """Record signals instead of sending them.

    ``_process_command_line`` is stubbed too: the manager only trusts a pid that
    ``/proc`` still calls a cloudflared, and there is no ``/proc`` on the machine
    running this suite.
    """

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        temporary_access, "_signal_process_group", lambda pid, number: sent.append((pid, number))
    )
    monkeypatch.setattr(
        temporary_access, "_process_command_line", lambda pid: f"/usr/local/bin/cloudflared ({pid})"
    )
    return sent


def _manager(tmp_path, clock, *, process=None, spawn=None, **kwargs):
    """A manager wired to fakes: never the module-level singleton, never a real child."""

    return temporary_access.TemporaryAccessManager(
        spawn=spawn or (lambda: process),
        clock=clock,
        poll_seconds=0.01,
        pid_file=str(tmp_path / "cloudflared.pid"),
        **kwargs,
    )


def _wait_for(predicate, *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _RecordingLogger:
    """Stand-in for the module logger so log assertions do not depend on global state.

    ``configure_logging()`` (imported with the app, and again on every lifespan) removes
    every handler from the root logger, and pytest's ``caplog`` handler lives there: a
    test that asserts on ``caplog.text`` passes alone and fails once an earlier test has
    built the app. Replacing the module's logger keeps the assertion about *this* module.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def _record(self, message: object, *args: object) -> None:
        self.messages.append(str(message) % args if args else str(message))

    debug = _record
    info = _record
    warning = _record
    error = _record
    exception = _record

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture()
def logs(monkeypatch) -> _RecordingLogger:
    recorder = _RecordingLogger()
    monkeypatch.setattr(temporary_access, "logger", recorder)
    return recorder


# --- 1. nothing is open until someone asks --------------------------------
def test_the_state_before_anything_happens_is_disabled(tmp_path, clock, signals) -> None:
    payload = _manager(tmp_path, clock).status()

    assert payload["status"] == "disabled"
    assert payload["url"] is None
    assert payload["started_at"] is None
    assert payload["expires_at"] is None
    assert payload["remaining_seconds"] is None
    assert payload["enabled"] is True
    # The answer names the one service this can ever expose.
    assert payload["target_url"] == settings.temporary_access_target_url
    assert payload["max_duration_seconds"] == 3600


# --- 2 + 3. it starts, and the address comes from cloudflared itself -------
def test_starting_reports_the_address_cloudflared_printed(tmp_path, clock, signals) -> None:
    process = FakeProcess(
        lines=("2026-10-03T09:00:00Z INF Requesting new quick Tunnel\n", URL_LINE)
    )
    manager = _manager(tmp_path, clock, process=process)

    started = manager.start()

    # The address is not known yet when start() answers: waiting for Cloudflare
    # would hold the request open and say nothing while it happened.
    assert started["status"] == "starting"
    assert started["expires_at"] is not None
    assert _wait_for(lambda: manager.status()["status"] == "active")

    payload = manager.status()
    assert payload["url"] == URL
    assert payload["detail"] is None
    assert payload["remaining_seconds"] == pytest.approx(3600, abs=2)
    assert signals == []


def test_a_noisy_line_still_yields_the_address(tmp_path, clock, signals) -> None:
    banner = "INF +" + "-" * 90 + "+"
    process = FakeProcess(lines=(f"{banner}\n", URL_LINE))

    manager = _manager(tmp_path, clock, process=process)
    manager.start()

    assert _wait_for(lambda: manager.status()["url"] == URL)
    assert manager.status()["status"] == "active"


# --- 4. one tunnel at a time ----------------------------------------------
def test_a_second_start_is_refused_while_one_is_running(tmp_path, clock, signals) -> None:
    manager = _manager(tmp_path, clock, process=FakeProcess(lines=(URL_LINE,)))
    manager.start()

    with pytest.raises(temporary_access.TemporaryAccessError) as excinfo:
        manager.start()

    assert excinfo.value.status_code == 409
    assert "already open" in excinfo.value.detail


# --- 5. closing really stops it --------------------------------------------
def test_closing_stops_the_child_and_clears_the_state(tmp_path, clock, signals) -> None:
    process = FakeProcess(lines=(URL_LINE,))
    manager = _manager(tmp_path, clock, process=process)
    manager.start()
    assert _wait_for(lambda: manager.status()["status"] == "active")

    payload = manager.stop()

    assert payload["status"] == "disabled"
    assert payload["url"] is None
    assert signals == [(process.pid, signal.SIGTERM)]
    assert process.exit_code == 0
    assert not (tmp_path / "cloudflared.pid").exists()


def test_closing_something_that_is_not_open_is_harmless(tmp_path, clock, signals) -> None:
    manager = _manager(tmp_path, clock)

    payload = manager.stop()

    assert payload["status"] == "disabled"
    assert signals == []


# --- 6. it closes itself when the deadline passes --------------------------
def test_a_tunnel_closes_itself_when_the_deadline_passes(
    tmp_path, clock, signals, monkeypatch, logs
) -> None:
    monkeypatch.setattr(settings, "temporary_access_max_duration_seconds", 60)
    process = FakeProcess(lines=(URL_LINE,))
    manager = _manager(tmp_path, clock, process=process)
    manager.start()
    assert _wait_for(lambda: manager.status()["status"] == "active")

    clock.advance(61)
    payload = manager.status()

    assert payload["status"] == "disabled"
    assert payload["url"] is None
    assert "time limit" in payload["detail"]
    assert signals == [(process.pid, signal.SIGTERM)]
    assert "Tunnel expired" in logs.text
    assert "Tunnel stopped (expired)" in logs.text


def test_a_start_that_never_reports_an_address_fails(
    tmp_path, clock, signals, monkeypatch, logs
) -> None:
    monkeypatch.setattr(settings, "temporary_access_start_timeout_seconds", 5)
    manager = _manager(tmp_path, clock, process=FakeProcess(lines=("INF no address yet\n",)))

    manager.start()
    assert manager.status()["status"] == "starting"

    clock.advance(6)
    payload = manager.status()

    assert payload["status"] == "error"
    assert "did not report a public address" in payload["detail"]
    assert signals == [(4242, signal.SIGTERM)]
    assert "Tunnel start failed" in logs.text


# --- 7. a missing binary is reported, not ignored --------------------------
def test_a_missing_cloudflared_is_reported(tmp_path, clock, signals, logs) -> None:
    def missing():
        raise FileNotFoundError(2, "No such file or directory", "cloudflared")

    manager = _manager(tmp_path, clock, spawn=missing)

    with pytest.raises(temporary_access.TemporaryAccessError) as excinfo:
        manager.start()

    assert excinfo.value.status_code == 503
    assert "cloudflared was not found" in excinfo.value.detail
    assert "Tunnel start failed" in logs.text
    # The failure is the state the settings page shows, not a silent nothing.
    payload = manager.status()
    assert payload["status"] == "error"
    assert "cloudflared was not found" in payload["detail"]


def test_a_child_that_cannot_be_spawned_is_reported(tmp_path, clock, signals) -> None:
    def broken():
        raise PermissionError("cloudflared is not executable")

    manager = _manager(tmp_path, clock, spawn=broken)

    with pytest.raises(temporary_access.TemporaryAccessError) as excinfo:
        manager.start()

    assert excinfo.value.status_code == 503
    assert manager.status()["status"] == "error"


# --- 8. an unexpected exit is visible -------------------------------------
def test_a_process_that_dies_on_its_own_becomes_an_error(tmp_path, clock, signals, logs) -> None:
    process = FakeProcess(lines=(URL_LINE,))
    manager = _manager(tmp_path, clock, process=process)
    manager.start()
    assert _wait_for(lambda: manager.status()["status"] == "active")

    process.exit_code = 137
    assert _wait_for(lambda: manager.status()["status"] == "error")

    payload = manager.status()
    assert "exited unexpectedly" in payload["detail"]
    assert "exit code 137" in payload["detail"]
    assert "Tunnel process exited unexpectedly" in logs.text
    assert not (tmp_path / "cloudflared.pid").exists()


# --- 9. a restart restores nothing and clears leftovers --------------------
def test_a_restart_starts_disabled_and_clears_a_leftover(tmp_path, clock, signals) -> None:
    pid_file = tmp_path / "cloudflared.pid"
    pid_file.write_text("999999\n", encoding="utf-8")
    manager = temporary_access.TemporaryAccessManager(clock=clock, pid_file=str(pid_file))

    # The new process knows nothing about the old tunnel: the state lives in memory
    # and a public door is never reopened by a restart.
    assert manager.status()["status"] == "disabled"

    assert manager.reap_orphans() == [999999]
    assert signals[0] == (999999, signal.SIGTERM)
    assert not pid_file.exists()


def test_a_recorded_pid_that_is_not_cloudflared_is_left_alone(tmp_path, clock, monkeypatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        temporary_access, "_signal_process_group", lambda pid, number: sent.append((pid, number))
    )
    monkeypatch.setattr(
        temporary_access, "_process_command_line", lambda pid: "/usr/bin/python -m http.server"
    )
    pid_file = tmp_path / "cloudflared.pid"
    # A pid file outlives a container's filesystem: the pid may now belong to
    # something else entirely, and killing it would be worse than a stray tunnel.
    pid_file.write_text("not-a-pid 999999\n", encoding="utf-8")
    manager = temporary_access.TemporaryAccessManager(clock=clock, pid_file=str(pid_file))

    assert manager.reap_orphans() == []
    assert sent == []


# --- 10. the API is behind the existing auth, like every other route -------
def test_the_api_requires_the_token_that_every_other_route_requires(client, monkeypatch) -> None:
    token = "temporary-access-token-0123456789"
    monkeypatch.setattr(settings, "api_auth_token", token)

    assert client.get(STATE_PATH).status_code == 401
    assert client.post(START_PATH).status_code == 401
    assert client.post(STOP_PATH).status_code == 401
    assert client.get(STATE_PATH, headers={"Authorization": f"Bearer {token}"}).status_code == 200


# --- the HTTP surface ------------------------------------------------------
@pytest.fixture()
def api_manager(monkeypatch, tmp_path, clock, signals):
    """The router reaches for the module-level manager; give it a fake instead."""

    stub = _manager(tmp_path, clock, process=FakeProcess(lines=(URL_LINE,)))
    monkeypatch.setattr(temporary_access_router, "manager", stub)
    return stub


def test_the_settings_page_can_open_and_close_a_tunnel(client, api_manager) -> None:
    opened = client.post(START_PATH)

    assert opened.status_code == 200
    assert opened.json()["status"] == "starting"
    assert opened.json()["url"] is None

    assert _wait_for(lambda: client.get(STATE_PATH).json()["status"] == "active")
    active = client.get(STATE_PATH).json()
    assert active["url"] == URL
    assert active["remaining_seconds"] > 0

    closed = client.post(STOP_PATH)
    assert closed.status_code == 200
    assert closed.json()["status"] == "disabled"
    assert closed.json()["url"] is None


def test_the_api_refuses_a_second_tunnel(client, api_manager) -> None:
    assert client.post(START_PATH).status_code == 200
    assert client.post(START_PATH).status_code == 409


def test_the_api_reports_a_missing_cloudflared(client, monkeypatch, tmp_path, clock, signals, logs):
    def missing():
        raise FileNotFoundError(2, "No such file or directory", "cloudflared")

    monkeypatch.setattr(
        temporary_access_router, "manager", _manager(tmp_path, clock, spawn=missing)
    )

    response = client.post(START_PATH)

    assert response.status_code == 503
    assert "cloudflared was not found" in response.json()["detail"]
    assert "Tunnel start failed" in logs.text


def test_the_deployment_can_switch_the_feature_off(client, monkeypatch, tmp_path, clock, signals):
    monkeypatch.setattr(settings, "temporary_access_enabled", False)
    monkeypatch.setattr(
        temporary_access_router, "manager", _manager(tmp_path, clock, process=FakeProcess())
    )

    response = client.post(START_PATH)

    assert response.status_code == 403
    assert "switched off" in response.json()["detail"]


def test_opening_and_closing_leave_an_audit_trail(client, api_manager, db_session) -> None:
    client.post(START_PATH)
    client.post(STOP_PATH)

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type.like("temporary_access_%")).order_by(AuditLog.id)
    ).all()

    assert [row.action for row in rows] == ["start", "stop"]
    assert all(row.entity_type == "temporary_access" for row in rows)
