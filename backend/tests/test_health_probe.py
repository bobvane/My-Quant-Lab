"""Guard: `/health` bounds every dependency probe it makes (ADR-069), and names the
schema revision it is running against (ADR-071).

The defect ADR-069 fixes: `celery_app.control.ping(timeout=1.0)` bounds how long we
wait for *replies*, not how long reaching the broker may take, so with no broker
running the worker probe sat for 9-12 seconds and held the whole response (and the
dashboard's first paint) with it. The connection is now opened under an explicit
deadline first; these tests assert that deadline is passed down, and that `unknown`
is the answer whenever the broker cannot be reached or nobody replies.

The defect ADR-071 fixes: `/health` called a database "connected" and the whole
service "healthy" without ever saying which schema it was serving — so an API that
had come up against an unmigrated database reported exactly what a good deployment
reports. `migration` now carries the revision, and `healthy` requires it.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.routers import health

celery_module = importlib.import_module("app.workers.celery_app")


class _FakeConnection:
    """Stands in for a kombu connection; records how it was asked to connect."""

    def __init__(self, *, reachable: bool = True, release_fails: bool = False) -> None:
        self.reachable = reachable
        self.release_fails = release_fails
        self.ensure_calls: list[dict] = []
        self.released = False

    def ensure_connection(self, **kwargs):
        self.ensure_calls.append(kwargs)
        if not self.reachable:
            raise OSError("Error 11001 connecting to broker: getaddrinfo failed")
        return self

    def release(self) -> None:
        if self.release_fails:
            raise OSError("already closed")
        self.released = True


class _FakeCelery:
    """Stands in for the Celery application object."""

    def __init__(self, connection: _FakeConnection, replies) -> None:
        self.connection = connection
        self.replies = replies
        self.read_kwargs: dict = {}
        self.ping_timeout: float | None = None
        self.pinged = False

    def connection_for_read(self, **kwargs):
        self.read_kwargs = kwargs
        return self.connection

    @property
    def control(self):
        return self

    def ping(self, timeout=None):
        self.pinged = True
        self.ping_timeout = timeout
        if isinstance(self.replies, Exception):
            raise self.replies
        return self.replies


def _patch_celery(monkeypatch, fake: _FakeCelery) -> _FakeCelery:
    monkeypatch.setattr(celery_module, "celery_app", fake)
    return fake


def test_the_broker_connection_is_opened_under_a_deadline(monkeypatch):
    connection = _FakeConnection()
    fake = _patch_celery(
        monkeypatch, _FakeCelery(connection, [{"celery@quantlab-app": {"ok": "pong"}}])
    )

    assert health._check_workers() == "1 online"

    # These two are the fix: an explicit connect/socket deadline and no retries.
    assert fake.read_kwargs["transport_options"] == {
        "socket_connect_timeout": health.PROBE_TIMEOUT_SECONDS,
        "socket_timeout": health.PROBE_TIMEOUT_SECONDS,
    }
    assert connection.ensure_calls == [{"max_retries": 0, "timeout": health.PROBE_TIMEOUT_SECONDS}]
    assert connection.released is True
    assert fake.ping_timeout == health.PROBE_TIMEOUT_SECONDS


def test_an_unreachable_broker_is_unknown_and_never_pinged(monkeypatch):
    connection = _FakeConnection(reachable=False)
    fake = _patch_celery(monkeypatch, _FakeCelery(connection, [{"x": {}}]))

    assert health._check_workers() == "unknown"
    assert fake.pinged is False
    # Even a failed connection attempt must be closed behind us.
    assert connection.released is True


def test_nobody_answering_is_zero_online(monkeypatch):
    _patch_celery(monkeypatch, _FakeCelery(_FakeConnection(), []))

    assert health._check_workers() == "0 online"


def test_a_ping_that_raises_is_unknown(monkeypatch):
    _patch_celery(monkeypatch, _FakeCelery(_FakeConnection(), RuntimeError("timed out")))

    assert health._check_workers() == "unknown"


def test_a_connection_that_will_not_close_does_not_change_the_answer(monkeypatch):
    """Cleanup trouble is not a dependency state: it must not become `unknown`."""

    _patch_celery(
        monkeypatch,
        _FakeCelery(_FakeConnection(release_fails=True), [{"celery@worker": {"ok": "pong"}}]),
    )

    assert health._check_workers() == "1 online"


def test_the_redis_probe_bounds_its_connect(monkeypatch):
    import redis

    recorded: dict = {}

    class _FakeClient:
        def ping(self) -> bool:
            return True

    def _from_url(url, **kwargs):
        recorded["url"] = url
        recorded.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(
        redis.Redis, "from_url", classmethod(lambda cls, url, **kw: _from_url(url, **kw))
    )

    assert health._check_redis() == "connected"
    assert recorded["socket_connect_timeout"] == health.PROBE_TIMEOUT_SECONDS
    assert recorded["socket_timeout"] == health.PROBE_TIMEOUT_SECONDS


def test_a_redis_that_refuses_is_unavailable(monkeypatch):
    import redis

    class _FakeClient:
        def ping(self) -> bool:
            raise ConnectionError("connection refused")

    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: _FakeClient()))

    assert health._check_redis() == "unavailable"


@pytest.mark.parametrize(
    ("workers", "expected"),
    [("1 online", "1 online"), ("0 online", "0 online"), ("unknown", "unknown")],
)
def test_health_still_publishes_the_same_keys(monkeypatch, client, workers, expected):
    monkeypatch.setattr(health, "_check_redis", lambda: "connected")
    monkeypatch.setattr(health, "_check_workers", lambda: workers)

    body = client.get("/api/v1/health").json()

    assert set(body) == {
        "status",
        "version",
        "database",
        "migration",
        "redis",
        "workers",
        "environment",
        "feature_version",
        "engine_version",
    }
    assert body["workers"] == expected


def _scratch_db(tmp_path, revision: str | None, *, with_table: bool = True):
    """A throwaway SQLite database standing in for the migrated one (ADR-071)."""

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'schema.sqlite3'}")
    with engine.begin() as conn:
        if with_table:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            if revision is not None:
                conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": revision}
                )
    return Session(engine)


def test_a_database_with_no_version_table_cannot_name_its_schema(tmp_path):
    db = _scratch_db(tmp_path, None, with_table=False)

    assert health._check_migration(db) == health.SCHEMA_REVISION_UNKNOWN


def test_an_empty_version_table_means_migrations_never_ran(tmp_path):
    db = _scratch_db(tmp_path, None)

    assert health._check_migration(db) == health.SCHEMA_REVISION_NONE


def test_a_stamped_version_table_reports_that_revision(tmp_path):
    db = _scratch_db(tmp_path, "0008_github_pending_review")

    assert health._check_migration(db) == "0008_github_pending_review"


@pytest.mark.parametrize(
    ("migration", "expected_status"),
    [
        ("0008_github_pending_review", "healthy"),
        (health.SCHEMA_REVISION_NONE, "degraded"),
        (health.SCHEMA_REVISION_UNKNOWN, "degraded"),
    ],
)
def test_health_is_only_healthy_for_a_schema_it_can_name(
    monkeypatch, client, migration, expected_status
):
    """A reachable database is not the same promise as a migrated one (ADR-071)."""

    monkeypatch.setattr(health, "_check_redis", lambda: "connected")
    monkeypatch.setattr(health, "_check_workers", lambda: "0 online")
    monkeypatch.setattr(health, "_check_migration", lambda db: migration)

    body = client.get("/api/v1/health").json()

    assert body["migration"] == migration
    assert body["status"] == expected_status
    assert body["database"] == "connected"


def test_the_dependency_probes_are_warmed_off_the_request_path(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(health, "_check_redis", lambda: calls.append("redis") or "connected")
    monkeypatch.setattr(health, "_check_workers", lambda: calls.append("workers") or "unknown")

    health.warm_dependency_probes().join(timeout=10)

    assert calls == ["redis", "workers"]


def test_a_warmup_that_fails_is_swallowed(monkeypatch):
    """Warming is an optimization: it must never take the process down with it."""

    def _boom():
        raise RuntimeError("nothing to connect to")

    monkeypatch.setattr(health, "_check_redis", _boom)
    monkeypatch.setattr(health, "_check_workers", _boom)

    thread = health.warm_dependency_probes()
    thread.join(timeout=10)

    assert thread.is_alive() is False


def test_the_app_warms_the_probes_when_it_starts(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import main as main_module

    calls: list[str] = []
    monkeypatch.setattr(main_module, "warm_dependency_probes", lambda: calls.append("warmed"))

    with TestClient(main_module.create_app()) as started:
        assert started.get("/api/v1/healthz").status_code == 200

    assert calls == ["warmed"]
