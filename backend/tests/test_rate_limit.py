"""Per-IP rate limiting for mutating requests (docs/14 §4)."""

from __future__ import annotations

from app.core.config import settings
from app.infrastructure.rate_limit import limiter


def _as_production(monkeypatch, limit: int) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", limit)
    monkeypatch.setattr(settings, "environment", "production")
    limiter.clear()


def test_mutating_burst_is_limited(client, monkeypatch) -> None:
    _as_production(monkeypatch, 3)
    try:
        codes = [
            client.post("/api/v1/strategies", json={"name": f"S{i}"}).status_code for i in range(5)
        ]
        assert codes == [201, 201, 201, 429, 429]
    finally:
        limiter.clear()


def test_last_429_has_retry_after(client, monkeypatch) -> None:
    _as_production(monkeypatch, 1)
    try:
        assert client.post("/api/v1/strategies", json={"name": "A"}).status_code == 201
        blocked = client.post("/api/v1/strategies", json={"name": "B"})
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"
        assert blocked.json()["error"]["code"] == "rate_limited"
    finally:
        limiter.clear()


def test_reads_are_not_limited(client, monkeypatch) -> None:
    _as_production(monkeypatch, 1)
    try:
        for _ in range(3):
            assert client.get("/api/v1/strategies").status_code == 200
    finally:
        limiter.clear()


def test_health_probe_is_never_limited(client, monkeypatch) -> None:
    _as_production(monkeypatch, 1)
    try:
        for _ in range(3):
            assert client.get("/api/v1/healthz").status_code == 200
    finally:
        limiter.clear()


def test_disabled_in_test_environment(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    # APP_ENVIRONMENT is "test" here, so the limiter must not engage.
    codes = [
        client.post("/api/v1/strategies", json={"name": f"T{i}"}).status_code for i in range(3)
    ]
    assert codes == [201, 201, 201]
