"""Celery application used by ``quantlab-worker`` and ``quantlab-scheduler``."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "quantlab",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_max_tasks_per_child=200,
    result_expires=86_400,
    beat_schedule={
        "scan-signals": {
            "task": "quantlab.scan_signals",
            "schedule": crontab(minute="*/15"),
        },
        "sync-market-data": {
            "task": "quantlab.sync_market_data",
            "schedule": crontab(minute=0, hour="*"),
        },
        # System Resource Monitor: 1-minute raw samples, deliberately cheap.
        "collect-resources": {
            "task": "quantlab.collect_resources",
            "schedule": crontab(minute="*"),
        },
        # Retention: raw 7d, rollups 30d (configurable in settings).
        "purge-resources": {
            "task": "quantlab.purge_resources",
            "schedule": crontab(minute=17, hour=3),
        },
    },
)
