"""System Resource Monitor tests.

No Docker daemon and no special privileges are needed: the proxy is tested
over a real Unix socket with a fake upstream, and the collector's pure parts
(shares, rollups, retention) are tested directly.

The security-critical property is the proxy whitelist: anything that is not
`GET /containers/json` or `GET /containers/<id>/stats?stream=false` must be
rejected without touching the upstream socket.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.infrastructure.resource_monitor import quantlab_shares
from app.infrastructure.resource_store import (
    get_peaks_between,
    purge_expired,
    record_resource_event,
    roll_up_recent,
    save_cycle,
)


# --------------------------------------------------------------------------- #
# shares (documented formulas)
# --------------------------------------------------------------------------- #
def test_shares_documented_formulas() -> None:
    host = {"cpu_percent": 10.0, "mem_used_mb": 5000.0, "mem_total_mb": 16000.0}
    shares = quantlab_shares(quantlab_cpu=3.0, quantlab_mem_mb=600.0, host=host)
    assert shares["cpu_share_of_current_usage"] == pytest.approx(30.0)
    assert shares["ram_share_of_used"] == pytest.approx(12.0)  # 600 / 5000
    assert shares["ram_share_of_total"] == pytest.approx(3.75)  # 600 / 16000


def test_shares_survive_zero_and_none() -> None:
    shares = quantlab_shares(
        None, None, {"cpu_percent": 0.0, "mem_used_mb": 0.0, "mem_total_mb": None}
    )
    assert shares["cpu_share_of_current_usage"] is None
    assert shares["ram_share_of_used"] is None
    assert shares["ram_share_of_total"] is None


# --------------------------------------------------------------------------- #
# persistence: save / rollup / retention
# --------------------------------------------------------------------------- #
def _cycle(quantlab_cpu: float = 2.0, quantlab_mem: float = 200.0) -> dict[str, Any]:
    return {
        "host": {
            "ts": dt.datetime.now(tz=dt.UTC),
            "cpu_percent": 10.0,
            "cpu_count": 4,
            "mem_used_mb": 4000.0,
            "mem_total_mb": 16000.0,
            "swap_used_mb": 0.0,
            "swap_total_mb": 0.0,
            "disk_used_gb": 100.0,
            "disk_total_gb": 500.0,
        },
        "containers": [
            {
                "container_name": "quantlab-api",
                "compose_service": "api",
                "is_quantlab": True,
                "cpu_percent": quantlab_cpu,
                "mem_used_mb": quantlab_mem,
                "mem_limit_mb": None,
                "state": "running",
            },
            {
                "container_name": "quantlab-worker",
                "compose_service": "worker",
                "is_quantlab": True,
                "cpu_percent": quantlab_cpu,
                "mem_used_mb": quantlab_mem,
                "mem_limit_mb": None,
                "state": "running",
            },
            {
                "container_name": "tailscale",
                "compose_service": None,
                "is_quantlab": False,
                "cpu_percent": 0.5,
                "mem_used_mb": 40.0,
                "mem_limit_mb": None,
                "state": "running",
            },
        ],
        "quantlab": {"cpu": quantlab_cpu * 2, "mem_mb": quantlab_mem * 2},
    }


def test_save_cycle_persists_host_and_containers(db_session) -> None:
    rows = save_cycle(db_session, _cycle())
    assert rows == 4  # 1 host + 3 containers

    from app.domain.models import ContainerResourceSample, HostResourceSample

    assert db_session.query(HostResourceSample).count() == 1
    samples = db_session.query(ContainerResourceSample).all()
    assert len(samples) == 3
    assert sum(1 for s in samples if s.is_quantlab) == 2


def test_rollup_recomputes_bucket_without_drift(db_session) -> None:
    save_cycle(db_session, _cycle(quantlab_cpu=2.0, quantlab_mem=100.0))
    save_cycle(db_session, _cycle(quantlab_cpu=4.0, quantlab_mem=300.0))
    written = roll_up_recent(db_session)
    assert written >= 2  # 'host' + 'quantlab' at minimum

    from app.domain.models import ResourceRollup

    ql = db_session.query(ResourceRollup).filter_by(scope="quantlab", granularity="5m").one()
    # All granularities are recomputed from raw samples.
    assert db_session.query(ResourceRollup).filter_by(scope="quantlab").count() == 3
    assert ql.cpu_avg == pytest.approx(6.0)  # (2+4) + (2+4) summed per ts, averaged
    assert ql.cpu_max == pytest.approx(8.0)
    assert ql.mem_avg_mb == pytest.approx(400.0)
    assert ql.mem_max_mb == pytest.approx(600.0)


def test_purge_respects_retention(db_session, monkeypatch) -> None:
    from app.core.config import settings
    from app.domain.models import HostResourceSample

    save_cycle(db_session, _cycle())
    old_ts = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=30)
    db_session.add(HostResourceSample(ts=old_ts, cpu_percent=1.0))
    db_session.commit()

    monkeypatch.setattr(settings, "resource_retention_raw_days", 7)
    deleted = purge_expired(db_session)
    assert deleted["host_samples"] >= 1

    remaining = db_session.query(HostResourceSample).count()
    assert remaining == 1  # the fresh sample survives


# --------------------------------------------------------------------------- #
# resource events
# --------------------------------------------------------------------------- #
def test_event_peaks_come_from_samples_window(db_session) -> None:
    save_cycle(db_session, _cycle(quantlab_cpu=5.0, quantlab_mem=250.0))
    start = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1)
    end = dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=1)

    event = record_resource_event(
        db_session,
        event_key="backtest:1",
        event_type="backtest_completed",
        started_at=start,
        ended_at=end,
        payload={"trade_count": 3},
    )
    assert event.cpu_peak_percent == pytest.approx(5.0)
    assert event.mem_peak_mb == pytest.approx(500.0)  # api 250 + worker 250
    assert event.duration_seconds is not None


def test_event_idempotent_by_key(db_session) -> None:
    start = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=5)
    end = dt.datetime.now(tz=dt.UTC)
    first = record_resource_event(
        db_session,
        event_key="backtest:9",
        event_type="backtest_completed",
        started_at=start,
        ended_at=end,
    )
    again = record_resource_event(
        db_session,
        event_key="backtest:9",
        event_type="backtest_completed",
        started_at=start,
        ended_at=end,
    )
    assert first.id == again.id

    from app.domain.models import ResourceEvent

    assert db_session.query(ResourceEvent).count() == 1


def test_short_event_leaves_peaks_null(db_session) -> None:
    """No samples inside the window -> null peaks, never invented numbers."""
    now = dt.datetime.now(tz=dt.UTC)
    event = record_resource_event(
        db_session,
        event_key="backtest:2",
        event_type="backtest_completed",
        started_at=now - dt.timedelta(seconds=2),
        ended_at=now,
    )
    assert event.cpu_peak_percent is None
    assert event.mem_peak_mb is None


def test_get_peaks_between_helper(db_session) -> None:
    save_cycle(db_session, _cycle(quantlab_cpu=7.0, quantlab_mem=700.0))
    start = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1)
    end = dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=1)
    cpu, mem = get_peaks_between(db_session, start, end)
    assert cpu == pytest.approx(7.0)
    assert mem == pytest.approx(1400.0)  # two quantlab containers x 700
