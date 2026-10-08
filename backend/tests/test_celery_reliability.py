"""The delivery contract: late ack, worker-lost rejection, and one clock.

A Redis broker forgets a message the instant it is handed to a worker, so Celery's
default ack-on-receipt turned every SIGKILLed child (OOM, ``docker kill``, an image
swap) into a lost task whose run row stayed ``running`` for ever — nothing
re-delivered it, and nothing reaped it. These tests pin the settings that close
that hole, and pin the schedule they must not disturb: the six business jobs that ship
keep their crontab literally, and ``reap-stuck-runs`` is the only addition (docs/33 §5,
§6.5). v2.6.0 removed the two resource-monitor jobs without touching either property.
"""

from __future__ import annotations

from celery.schedules import crontab

from app.workers.celery_app import celery_app

#: task name -> crontab, exactly as v2.5.0 shipped it. Any change here is a change
#: to when the product does its work, which is not what reliability work gets to do.
SHIPPED_SCHEDULE = {
    "scan-signals": ("quantlab.scan_signals", crontab(minute="*/15")),
    "notify-signals": ("quantlab.notify_signals", crontab(minute="5,20,35,50")),
    "sync-market-data": ("quantlab.sync_market_data", crontab(minute=0, hour="*")),
    "evaluate-signal-outcomes": (
        "quantlab.evaluate_signal_outcomes",
        crontab(minute="*/30"),
    ),
    "evaluate-strategy-lifecycle": (
        "quantlab.evaluate_strategy_lifecycle",
        crontab(minute=30, hour=3),
    ),
    "check-github-sources": ("quantlab.check_github_sources", crontab(minute=10, hour=4)),
}

REAPER = "reap-stuck-runs"


def test_celery_reliability_defaults() -> None:
    """Every setting that decides whether a lost task is re-delivered or eaten."""

    conf = celery_app.conf

    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    # Explicit, not inherited: the intent is "a raising task is dropped, redelivery
    # is what makes duplicates safe", and this line is where that is decided.
    assert conf.task_acks_on_failure_or_timeout is True
    # Late ack without a prefetch of one means a worker still holds unacked messages
    # it would lose. The two only work together.
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options == {"visibility_timeout": 7200}

    # The window has to sit above the hard limit, or Redis hands a task that is
    # still legitimately running to a second worker.
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit

    # The delivery change must not have moved anything else about the tasks.
    assert conf.task_time_limit == 3600
    assert conf.task_soft_time_limit == 3300
    assert conf.task_track_started is True
    assert conf.result_expires == 86_400
    assert conf.timezone == "UTC"
    assert conf.enable_utc is True
    assert conf.accept_content == ["json"]


def test_no_task_is_excused_from_late_ack() -> None:
    """One lossy collector used to opt out; v2.6.0 deleted it together with the task."""

    from app.workers import tasks as worker_tasks  # noqa: F401 - registers the tasks

    annotations = celery_app.conf.task_annotations
    assert not annotations, annotations
    # The retired resource-monitor jobs must not be registered behind Beat's back.
    assert "quantlab.collect_resources" not in celery_app.tasks
    assert "quantlab.purge_resources" not in celery_app.tasks


def test_beat_schedule_is_unchanged_except_the_reaper() -> None:
    schedule = celery_app.conf.beat_schedule

    assert set(schedule) == set(SHIPPED_SCHEDULE) | {REAPER}
    for name, (task, when) in SHIPPED_SCHEDULE.items():
        assert schedule[name]["task"] == task, name
        assert schedule[name]["schedule"] == when, name

    assert schedule[REAPER]["task"] == "quantlab.reap_stuck_runs"
    assert schedule[REAPER]["schedule"] == crontab(minute="*/5")

    # The two resource-monitor jobs were deleted in v2.6.0; Beat must not know them.
    assert "collect-resources" not in schedule
    assert "purge-resources" not in schedule
    assert len(schedule) == 7, sorted(schedule)


def test_the_reaper_is_a_registered_task() -> None:
    """Beat naming a task nobody registered would fail at dispatch time, in production."""

    from app.workers import tasks as worker_tasks  # noqa: F401 - registers the tasks

    assert "quantlab.reap_stuck_runs" in celery_app.tasks
