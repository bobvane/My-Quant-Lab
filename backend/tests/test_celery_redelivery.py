"""Test A: a SIGKILLed prefork child must hand its in-flight task back.

docs/33 §5 (v2.6.0 P0) claims a specific chain:

    message delivered -> child accepted it -> child dies by SIGKILL
        -> `WorkerLostError` -> `Request.on_failure`
        -> `reject(requeue=True)` -> the SAME task id is delivered again
        -> the second attempt succeeds -> exactly one business result

`tests/test_celery_reliability.py` only asserts the four settings that are
supposed to produce that chain. This module asserts the chain itself, against a
real Redis and a real prefork worker, because the settings can be right while the
mechanism is not (and because "the worker restarted and the task ran again" is
NOT the claim — the claim is that the *same Celery task id* is re-consumed).

Gating: the test needs a broker, so it skips unless ``TEST_REDIS_URL`` is set —
the ordinary suite must not require Redis (`tests/test_postgres_triggers.py`
does the same for Postgres). CI sets it in the ``celery redelivery`` job.

The probe task deliberately lives in a worker bootstrap script generated at run
time (``_WORKER_BOOTSTRAP``) instead of ``app/workers/tasks.py``: production
images must not contain test-only tasks, and no production task is long enough to
be interrupted reliably (a backtest finishes in ~0.5 s). The worker itself is the
real ``app.workers.celery_app`` object, so the delivery settings under test are
the production ones.

Two failure paths are deliberately distinguished, because only one of them is a
redelivery failure:

* path A — the child had accepted the job before the kill. The worker logs
  ``WorkerLostError('Worker exited prematurely: ...')`` and must requeue.
* path B — the kill landed before the child accepted the job. Billiard never
  fires the job callback in that branch, so nothing is requeued and nothing is
  logged: the test fails with "task was not accepted before child termination"
  rather than blaming the redelivery mechanism.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

try:  # `redis` ships in backend/requirements.txt; be defensive anyway.
    import redis
except ImportError:  # pragma: no cover - only on a broken environment
    redis = None  # type: ignore[assignment]

from celery import Celery

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")

# The child is SIGKILLed while the probe sleeps, so the hold must outlast the
# test's own "started, settled, killed" sequence (~1.5 s) with a wide margin.
HOLD_SECONDS = 12
# `celery/concurrency/base.py` runs `accept_callback` inside the child *before*
# the task body, so a written "started" record means the accept was already sent.
# This window only absorbs the parent reading that accept off the pipe.
SETTLE_SECONDS = 1.0
# billiard reports a lost worker only after its own LOST_WORKER_TIMEOUT (10.0 s),
# so every wait below is a poll with a budget, never a fixed sleep.
START_TIMEOUT = 15.0
REQUEUE_TIMEOUT = 30.0
COMPLETION_TIMEOUT = 40.0
WORKER_EXIT_TIMEOUT = 20.0
POLL_INTERVAL = 0.2

needs_redis = pytest.mark.skipif(
    not TEST_REDIS_URL,
    reason="TEST_REDIS_URL not set (a real broker and a real worker are required)",
)
needs_posix = pytest.mark.skipif(
    os.name != "posix",
    reason="the kill targets a POSIX prefork child (SIGKILL)",
)
needs_client = pytest.mark.skipif(
    redis is None,
    reason="the redis client library is not installed",
)

_WORKER_BOOTSTRAP = '''\
"""Worker started by tests/test_celery_redelivery.py; never shipped or imported."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

EVIDENCE = Path(os.environ["REDELIVERY_EVIDENCE"])
RESULT = Path(os.environ["REDELIVERY_RESULT"])
TASK_NAME = os.environ["REDELIVERY_TASK"]

# cwd is backend/ (see the Popen call) and PYTHONPATH points there too; be explicit
# so a changed cwd cannot silently import a different `app` package.
sys.path.insert(0, os.environ.get("REDELIVERY_BACKEND", os.getcwd()))

from app.workers.celery_app import celery_app  # noqa: E402


def _record(event, **fields):
    line = json.dumps({"event": event, "at": time.time(), **fields}, sort_keys=True)
    with EVIDENCE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\\n")
        handle.flush()
        os.fsync(handle.fileno())


def _started_so_far():
    try:
        lines = EVIDENCE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return 0
    return sum(1 for line in lines if '"started"' in line)


@celery_app.task(bind=True, name=TASK_NAME)
def redelivery_probe(self, hold_seconds):
    """Attempt 1 is killed mid-sleep; attempt 2 finishes and writes one result."""
    attempt = _started_so_far() + 1
    _record("started", task_id=self.request.id, pid=os.getpid(), attempt=attempt)
    time.sleep(hold_seconds)
    try:
        # O_EXCL is the whole idempotency claim of this probe: exactly one attempt
        # can create the result file, however many times the task is delivered.
        handle = os.fdopen(
            os.open(RESULT, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644),
            "w",
            encoding="utf-8",
        )
    except FileExistsError:
        _record(
            "result_duplicate_suppressed",
            task_id=self.request.id,
            pid=os.getpid(),
            attempt=attempt,
        )
    else:
        with handle:
            json.dump(
                {"task_id": self.request.id, "pid": os.getpid(), "attempt": attempt},
                handle,
                sort_keys=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        _record("result_written", task_id=self.request.id, pid=os.getpid(), attempt=attempt)
    _record("completed", task_id=self.request.id, pid=os.getpid(), attempt=attempt)
    return {"task_id": self.request.id, "attempt": attempt}


if __name__ == "__main__":
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            # Only this test's queue: the worker must not touch anything else in
            # the shared Redis database.
            "--queues",
            sys.argv[1],
            "--concurrency",
            "1",
            # Prefork explicitly: the claim under test is about a *child* dying, and
            # the pool type must not depend on a future default. Prefetch stays at
            # the production conf value (`worker_prefetch_multiplier=1`) on purpose:
            # the test must prove the real configuration, not a tuned copy of it.
            "--pool",
            "prefork",
            "--hostname",
            os.environ["REDELIVERY_NODE"],
            # Nothing to coordinate with and no long startup: this is one worker.
            "--without-gossip",
            "--without-mingle",
        ]
    )
'''


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _records(evidence: Path, event: str | None = None) -> list[dict]:
    """Every JSON record written by the probe, optionally filtered by event."""
    try:
        lines = evidence.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    parsed = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:  # a partially flushed line; the next poll will see it
            continue
        if not isinstance(record, dict):
            continue
        if event is None or record.get("event") == event:
            parsed.append(record)
    return parsed


def _wait_for(describe, probe, timeout: float):
    """Poll ``probe`` until it returns something truthy or the budget runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(POLL_INTERVAL)
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _ppid_of(pid: int) -> int | None:
    """Parent pid from /proc/<pid>/stat (comm can contain spaces and parens)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (IndexError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - same user in practice
        return True
    return True


def _processes_in_group(pgid: int) -> list[int]:
    """Every live pid whose process group is ``pgid`` (the worker's own session)."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
            pgrp = int(stat.rsplit(")", 1)[1].split()[3])  # state ppid pgrp
        except (OSError, IndexError, ValueError):
            continue
        if pgrp == pgid:
            found.append(pid)
    return found


def _redis_client():
    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    client.ping()
    return client


def _unacked(client) -> int:
    try:
        return int(client.hlen("unacked"))
    except Exception:  # pragma: no cover - diagnostics must never mask the failure
        return -1


def _start_worker(script: Path, queue: str, node: str, env: dict, log_path: Path):
    handle = log_path.open("wb")
    try:
        process = subprocess.Popen(
            [sys.executable, str(script), queue],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group: clean teardown, no orphans
        )
    except BaseException:
        handle.close()
        raise
    return process, handle


def _stop_worker(process) -> int | None:
    """SIGTERM the worker's group, escalating to SIGKILL; never raises."""
    if process is None:
        return None
    if process.poll() is not None:
        return process.returncode
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        return process.wait(timeout=WORKER_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        return None
    return None


def _cleanup_redis(client, queue: str) -> None:
    """Remove this run's keys only; never flush a database we did not create."""
    with contextlib.suppress(Exception):  # teardown must not mask the real assertion
        client.delete(
            queue,
            f"_kombu.binding.{queue}",
            "unacked",
            "unacked_index",
            "unacked_mutex",
        )


# --------------------------------------------------------------------------- #
# the test
# --------------------------------------------------------------------------- #
@needs_posix
@needs_redis
@needs_client
def test_a_sigkilled_prefork_child_requeues_the_same_task_id(tmp_path):
    token = uuid.uuid4().hex[:12]
    queue = f"redelivery-{token}"
    node = f"redelivery-{token}@localhost"
    task_name = f"tests.redelivery_probe_{token}"
    evidence = tmp_path / "evidence.jsonl"
    result = tmp_path / "result.json"
    log_path = tmp_path / "worker.log"
    script = tmp_path / "redelivery_worker.py"
    script.write_text(_WORKER_BOOTSTRAP, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "CELERY_BROKER_URL": TEST_REDIS_URL,
            "CELERY_RESULT_BACKEND": TEST_REDIS_URL,
            "REDIS_URL": TEST_REDIS_URL,
            "REDELIVERY_BACKEND": str(BACKEND_DIR),
            "REDELIVERY_EVIDENCE": str(evidence),
            "REDELIVERY_RESULT": str(result),
            "REDELIVERY_TASK": task_name,
            "REDELIVERY_NODE": node,
        }
    )

    client = _redis_client()
    client.delete(queue, f"_kombu.binding.{queue}")

    producer = Celery("redelivery-sender", broker=TEST_REDIS_URL, backend=TEST_REDIS_URL)
    producer.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])

    process = None
    log_handle = None
    pgid = None
    task_id = None
    first_child_pid = None
    worker_exit = None
    leftovers: list[int] = []
    try:
        process, log_handle = _start_worker(script, queue, node, env, log_path)
        pgid = os.getpgid(process.pid)

        task_id = producer.send_task(task_name, args=[HOLD_SECONDS], queue=queue).id

        started = _wait_for("started", lambda: _records(evidence, "started"), START_TIMEOUT)
        if not started:
            pytest.fail(
                "the task never reached a prefork child\n"
                + _diagnose(
                    "no attempt started", task_id, process, evidence, log_path, client, queue
                )
            )
        assert [record["task_id"] for record in started] == [task_id], (
            "the child ran a task the test did not send: " + json.dumps(started)
        )

        first_child_pid = int(started[0]["pid"])
        assert first_child_pid != process.pid, (
            "the probe ran in the worker main process, not a prefork child"
        )
        assert _ppid_of(first_child_pid) == process.pid, (
            f"pid {first_child_pid} is not a prefork child of worker pid {process.pid} "
            f"(ppid={_ppid_of(first_child_pid)}); refusing to kill an unverified process"
        )

        # Let the parent consume the accept that the child already sent, then kill
        # the one process the test owns. Killing the worker main process instead
        # would test a different (graceful) path entirely.
        time.sleep(SETTLE_SECONDS)
        assert _process_alive(first_child_pid), "the child died before the test could SIGKILL it"
        assert _unacked(client) >= 1, (
            "the in-flight delivery is not in Redis' unacked hash, so the first attempt cannot be "
            "proven un-acked: " + json.dumps(_records(evidence))
        )
        os.kill(first_child_pid, signal.SIGKILL)

        second = _wait_for(
            "second started",
            lambda: _records(evidence, "started")[1:2],
            REQUEUE_TIMEOUT,
        )
        if not second:
            log_text = _read_text(log_path)
            if "WorkerLostError" in log_text or "exited prematurely" in log_text:
                pytest.fail(
                    "the lost child did report WorkerLostError, but the same task id was never "
                    "delivered again: reject(requeue=True) did not put the message back\n"
                    + _diagnose(
                        "redelivery missing", task_id, process, evidence, log_path, client, queue
                    )
                )
            pytest.fail(
                "task was not accepted before child termination: the SIGKILL landed before the "
                "child acknowledged the job, so the redelivery path was never exercised (no "
                "WorkerLostError in the worker log). Re-run the test; this is a timing failure, "
                "not a broken worker\n"
                + _diagnose("unaccepted kill", task_id, process, evidence, log_path, client, queue)
            )

        completed = _wait_for(
            "second completion",
            lambda: _records(evidence, "completed"),
            COMPLETION_TIMEOUT,
        )
        if not completed:
            pytest.fail(
                "the redelivered task never finished\n"
                + _diagnose(
                    "second attempt incomplete", task_id, process, evidence, log_path, client, queue
                )
            )

        # --- the assertions the chain above exists to make ------------------- #
        records = _records(evidence)
        started_records = [record for record in records if record["event"] == "started"]
        completed_records = [record for record in records if record["event"] == "completed"]
        suppressed = [
            record for record in records if record["event"] == "result_duplicate_suppressed"
        ]

        assert {record["task_id"] for record in records} == {task_id}, (
            "more than one Celery task id ran; a redelivery must reuse the same id: "
            + json.dumps(records)
        )
        assert len(started_records) == 2, (
            f"expected exactly two attempts, got {json.dumps(started_records)}"
        )
        assert started_records[0]["pid"] != started_records[1]["pid"], (
            "the second attempt ran in the killed child, so the redelivery was not a new process: "
            + json.dumps(started_records)
        )
        assert (
            len(completed_records) == 1 and completed_records[0]["pid"] == started_records[1]["pid"]
        ), "the surviving result did not come from the second attempt: " + json.dumps(records)
        assert not suppressed, "the probe wrote its single result twice: " + json.dumps(suppressed)

        unique_result = json.loads(result.read_text(encoding="utf-8"))
        assert unique_result["task_id"] == task_id
        assert unique_result["attempt"] == 2, (
            f"the result came from attempt {unique_result['attempt']}"
        )

        # The second attempt acked: an in-flight message must not stay unacked.
        drained = _wait_for("unacked drained", lambda: _unacked(client) == 0, 10.0)
        assert drained, f"the second attempt never acknowledged; unacked={_unacked(client)}"

        log_text = _read_text(log_path)
        assert "WorkerLostError" in log_text and "Worker exited prematurely" in log_text, (
            "the worker log does not show the lost-child path this test exists to prove\n"
            + _diagnose(
                "no WorkerLostError in the log", task_id, process, evidence, log_path, client, queue
            )
        )
        assert "SIGKILL" in log_text, "the log does not name the signal that killed the child"

        print(
            f"redelivery proven: task_id={task_id} attempts={len(started_records)} "
            f"pids={[record['pid'] for record in started_records]} "
            f"task_id mentions in the worker log={log_text.count(task_id)}"
        )
    finally:
        worker_exit = _stop_worker(process)
        if pgid is not None:
            # Give a terminating pool a moment to finish before calling it a leak.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                leftovers = [pid for pid in _processes_in_group(pgid) if pid != os.getpid()]
                if not leftovers:
                    break
                time.sleep(POLL_INTERVAL)
            for pid in leftovers:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
        _cleanup_redis(client, queue)
        if log_handle is not None:
            log_handle.close()
        if os.environ.get("REDELIVERY_DEBUG") or worker_exit != 0 or leftovers:
            print(_diagnose("teardown", task_id, process, evidence, log_path, client, queue))

    # Teardown is part of the contract: the job must not leak processes.
    assert worker_exit == 0, f"the worker did not exit cleanly after SIGTERM (exit={worker_exit})"
    assert leftovers == [], f"prefork children survived the worker: {leftovers}"


def _diagnose(
    what: str, task_id, process, evidence: Path, log_path: Path, client, queue: str
) -> str:
    """Everything needed to tell the two failure paths apart, printed on failure."""
    lines = [f"--- celery redelivery diagnostics: {what} ---"]
    lines.append(f"task_id={task_id}")
    exit_code = process.poll() if process else None
    lines.append(
        f"worker_pid={getattr(process, 'pid', None)} worker_exit={exit_code} queue={queue}"
    )
    lines.append(f"redis={TEST_REDIS_URL}")
    lines.append(
        f"unacked_hlen={_unacked(client)} queue_llen={client.llen(queue) if client else 'n/a'}"
    )
    for record in _records(evidence):
        lines.append("record: " + json.dumps(record, sort_keys=True))
    tail = _read_text(log_path).splitlines()[-60:]
    lines.append("--- worker log (last 60 lines) ---")
    lines.extend(tail)
    return "\n".join(lines)
