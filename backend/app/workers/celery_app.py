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
    # --- Delivery semantics (v2.6.0 P0, docs/33 §5) -----------------------
    # Ack AFTER the task returns. Redis drops a message as soon as it is handed
    # out, so with the old ack-on-receipt default a SIGKILLed child (OOM, `docker
    # kill`, an image swap) took the task with it and *nothing* re-delivered it:
    # the run row stayed `running` for ever.
    task_acks_late=True,
    # A lost child is a failed delivery, not a successful one — without this even
    # a graceful child loss eats the task instead of requeueing it.
    task_reject_on_worker_lost=True,
    # Explicit rather than implicit: a task that RAISES is acked and dropped. An
    # automatic retry of `run_research` would spend money twice, and every
    # periodic task has a next tick. Duplicates are made safe in `tasks.py` and
    # the services, not by refusing to redeliver.
    task_acks_on_failure_or_timeout=True,
    # concurrency 2 x multiplier 1 = two reserved-but-unacked messages per worker.
    # That is two chances of loss/duplication per kill, for pipelining we do not
    # need at this scale.
    worker_prefetch_multiplier=1,
    # Redis's visibility window defaults to 3600 s — exactly our task_time_limit,
    # so a task still running at the limit could be handed to a second worker.
    # Sit strictly above the limit plus a queue wait.
    broker_transport_options={"visibility_timeout": 7200},
    # The one deliberately lossy task: `collect_resources` writes sample rows that
    # have no unique constraint (`domain/models.py`), so a redelivery would double
    # them. It is deleted with the whole feature in Step 3, so it is excluded from
    # late ack instead of being hardened.
    task_annotations={"quantlab.collect_resources": {"acks_late": False}},
    beat_schedule={
        "scan-signals": {
            "task": "quantlab.scan_signals",
            "schedule": crontab(minute="*/15"),
        },
        # Deliver any newly persisted signal; runs just after the scan so a slow
        # or failing webhook can never delay signal generation.
        "notify-signals": {
            "task": "quantlab.notify_signals",
            "schedule": crontab(minute="5,20,35,50"),
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
        # Signal outcome tracking: look forward in price data for pending signals.
        "evaluate-signal-outcomes": {
            "task": "quantlab.evaluate_signal_outcomes",
            "schedule": crontab(minute="*/30"),
        },
        # Strategy lifecycle: apply evidence-gated promotions/degradations daily.
        "evaluate-strategy-lifecycle": {
            "task": "quantlab.evaluate_strategy_lifecycle",
            "schedule": crontab(minute=30, hour=3),
        },
        # GitHub watcher: re-import watched strategies when a new commit lands.
        "check-github-sources": {
            "task": "quantlab.check_github_sources",
            "schedule": crontab(minute=10, hour=4),
        },
        # Stuck-run recovery (docs/33 §6): fail a run whose worker is gone, so no
        # row claims to be `running` for ever. This also replaces
        # `collect-resources` as the worker/Beat liveness signal, because it fires
        # every five minutes and touches the schedule file every time.
        "reap-stuck-runs": {
            "task": "quantlab.reap_stuck_runs",
            "schedule": crontab(minute="*/5"),
        },
    },
)
