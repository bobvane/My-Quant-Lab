"""Signal notification service (M11, docs/09_SIGNAL_ENGINE.md).

The pipeline documented in docs/09 §2 ends with ``persist signal → AI explain →
notify``. This module is the ``notify`` step. It is deliberately conservative:

* **de-duplication**: a signal is notified once. ``Signal.notified_at`` is the
  receipt, so re-running the worker never double-sends.
* **noise control** (docs/09 §7): only BUY/SELL by default, optional WAIT,
  quiet hours, a daily cap and a per-series cooldown.
* **audit**: every send, failure and suppression is written to ``audit_logs``.
* **no facts invented**: the payload only carries values the engine already
  computed on the signal row. AI text is never presented as a promise.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.data.strategy_service import record_audit
from app.domain.models import Asset, AuditLog, Signal, Strategy, StrategyVersion
from app.notifications.channels import build_provider
from app.notifications.config import (
    NotificationConfig,
    get_notification_config,
)
from app.notifications.provider import NotificationConfigError, NotificationError

logger = logging.getLogger(__name__)

__all__ = [
    "NotificationMessage",
    "build_signal_payload",
    "notify_pending_signals",
    "send_test_notification",
]

DISCLAIMER = "研究信息，仅供参考，不构成投资建议；本系统不会自动下单。"
EVENT_NOTIFIED = "signal_notified"
EVENT_FAILED = "signal_notification_failed"
EVENT_SUPPRESSED = "signal_notification_suppressed"


@dataclass
class NotificationMessage:
    title: str
    body: str
    meta: dict[str, Any] = field(default_factory=dict)


def _reason(signal: Signal) -> str:
    rules = signal.triggered_rules_json or []
    if signal.state == "WAIT":
        return "进入条件尚未确认（等待）"
    if signal.state == "NO_SIGNAL":
        return "当前没有候选事件"
    if rules:
        return "触发规则：" + ", ".join(str(r) for r in rules)
    return f"{signal.state} 条件满足"


def _signal_link(config: NotificationConfig, signal: Signal) -> str:
    base = config.base_url.rstrip("/")
    return f"{base}/?signal={signal.id}" if base else f"/?signal={signal.id}"


def build_signal_payload(
    db: Session, signal: Signal, config: NotificationConfig
) -> NotificationMessage:
    """Build the notification for one signal from engine-computed fields only."""

    asset = db.get(Asset, signal.asset_id)
    version = db.get(StrategyVersion, signal.strategy_version_id)
    strategy = db.get(Strategy, version.strategy_id) if version else None
    symbol = asset.symbol if asset else str(signal.asset_id)
    strategy_name = strategy.name if strategy else "strategy"
    reason = _reason(signal)

    title = f"[{signal.state}] {symbol} {signal.timeframe} — {strategy_name}"
    body = f"{symbol} 在 {signal.timeframe} 触发 {signal.state}。{reason}。（{DISCLAIMER}）"
    meta = {
        "event": "signal",
        "signal_id": signal.id,
        "symbol": symbol,
        "signal": signal.state,
        "direction": signal.direction,
        "strategy": {
            "id": strategy.id if strategy else None,
            "name": strategy_name,
            "version_id": signal.strategy_version_id,
            "version": version.version if version else None,
        },
        "timeframe": signal.timeframe,
        "timestamp": signal.bar_timestamp.isoformat(),
        "generated_at": signal.generated_at.isoformat() if signal.generated_at else None,
        "price_reference": _as_float(signal.price_reference),
        "stop_reference": _as_float(signal.stop_reference),
        "target_reference": _as_float(signal.target_reference),
        "reason": reason,
        "triggered_rules": list(signal.triggered_rules_json or []),
        "link": _signal_link(config, signal),
        "channel": "webhook",
        "disclaimer": DISCLAIMER,
    }
    return NotificationMessage(title=title, body=body, meta=meta)


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _day_start(moment: dt.datetime) -> dt.datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; PostgreSQL hands back aware ones."""

    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _load_recent_sends(
    db: Session, since: dt.datetime | None, *, now: dt.datetime
) -> tuple[dict[tuple[int, int], dt.datetime], int]:
    """Return (per-series last-send index, count sent today).

    The daily count is independent of the cooldown window: it always counts
    sends since UTC midnight, otherwise a zero cooldown would silently disable
    the daily cap.
    """

    daily_start = _day_start(now)
    floor = min(bound for bound in (since, daily_start) if bound is not None)
    rows = db.scalars(
        select(AuditLog).where(
            AuditLog.event_type == EVENT_NOTIFIED,
            AuditLog.created_at >= floor,
        )
    ).all()
    index: dict[tuple[int, int], dt.datetime] = {}
    sent_today = 0
    for row in rows:
        created = _ensure_utc(row.created_at)
        if created >= daily_start:
            sent_today += 1
        if since is None or created < since:
            continue
        payload = row.payload_json or {}
        key = (payload.get("strategy_version_id"), payload.get("asset_id"))
        if all(isinstance(part, int) for part in key):
            prior = index.get(key)  # type: ignore[arg-type]
            if prior is None or created > prior:
                index[key] = created  # type: ignore[index]
    return index, sent_today


def _audit(
    db: Session,
    event_type: str,
    signal: Signal,
    *,
    symbol: str | None,
    channel: str | None = None,
    channels: list[str] | None = None,
    detail: str | None = None,
) -> None:
    action = {
        EVENT_NOTIFIED: "notify",
        EVENT_SUPPRESSED: "notify_suppressed",
    }.get(event_type, "notify_failed")
    payload: dict[str, Any] = {
        "signal_id": signal.id,
        "strategy_version_id": signal.strategy_version_id,
        "asset_id": signal.asset_id,
        "symbol": symbol,
        "state": signal.state,
        "timeframe": signal.timeframe,
    }
    if channel:
        payload["channel"] = channel
    if channels:
        payload["channels"] = channels
    if detail:
        payload["detail"] = detail
    record_audit(
        db,
        event_type=event_type,
        entity_type="signal",
        entity_id=str(signal.id),
        action=action,
        payload=payload,
    )


def _candidate_lock(db: Session) -> dict[str, Any]:
    """``FOR UPDATE SKIP LOCKED`` where the database can do it.

    PostgreSQL is the deployment; SQLite (the test suite) has no row locks and
    would reject the clause outright, so the guard is on the dialect. This and
    ``workers/tasks.py:_lock_github_source`` are the only two places the
    repository takes a row lock.
    """

    if db.get_bind().dialect.name == "postgresql":
        return {"skip_locked": True}
    return {}


def _claim_signal(db: Session, signal_id: int, moment: dt.datetime) -> Signal | None:
    """Take the exclusive, durable claim that makes a send at-most-once.

    Two things happen here, in this order, and the order is the whole point
    (docs/33 §7.3):

    1. the row is locked (``SKIP LOCKED``), so a second runner cannot even read it
       while this one decides — that is the concurrency guarantee;
    2. ``notified_at`` is written and **committed before anything is sent**, so the
       decision is durable, not a value that a killed process takes with it.

    What that trades away: a process killed between the claim and the outbound call
    loses that notification. That is the choice this deployment makes — a duplicate
    alert to a human is worse than a missed one that is still visible in the signal
    list with its ``notified_at`` receipt — and it is recorded here rather than in a
    comment nobody reads. A send whose *every* channel fails is released by the
    caller, because no message existed to duplicate.
    """

    statement = select(Signal).where(Signal.id == signal_id, Signal.notified_at.is_(None))
    claimed = db.scalars(statement.with_for_update(**_candidate_lock(db))).one_or_none()
    if claimed is None:
        # Another runner owns it (locked) or has already claimed it (notified).
        db.rollback()
        return None
    claimed.notified_at = moment
    db.commit()
    return claimed


def _release_signal(db: Session, signal_id: int) -> None:
    """Give back a claim that produced no message, so a later tick may retry it.

    Only ever called when nothing was delivered, so releasing cannot duplicate
    anything — it can only restore a notification that would otherwise be lost.
    The caller owns the commit, so the release and the audit entry that explains
    it land together.
    """

    db.rollback()
    db.execute(
        update(Signal)
        .where(Signal.id == signal_id, Signal.notified_at.is_not(None))
        .values(notified_at=None)
    )


def notify_pending_signals(db: Session, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Send notifications for every eligible, not-yet-notified signal.

    Idempotent: signals carry ``notified_at`` once handled, so repeated scans or
    worker restarts never produce duplicate notifications. The write happens
    *before* the outbound call and under a row lock, which is what closes the
    window a killed or concurrent worker used to be able to double-send through.
    """

    moment = now or dt.datetime.now(tz=dt.UTC)
    config = get_notification_config(db)
    summary: dict[str, Any] = {
        "enabled": config.enabled,
        "candidates": 0,
        "sent": 0,
        "failed": 0,
        "suppressed": 0,
        "skipped_quiet_hours": 0,
        "skipped_cooldown": 0,
        "skipped_claimed": 0,
    }
    if not config.configured:
        return summary

    candidates = db.scalars(
        select(Signal)
        .where(
            Signal.notified_at.is_(None),
            Signal.state.in_(config.eligible_states),
            # A signal the user already acknowledged is handled. Pushing it anyway
            # made "acknowledge" mean nothing on the dashboard (ADR-115);
            # ``notified_at`` keeps recording that it *was* pushed.
            Signal.status != "acknowledged",
        )
        .order_by(Signal.id)
    ).all()
    # Never dump a backlog: only signals generated after notifications were
    # last enabled are eligible.
    enabled_at = config.enabled_at
    pending = [
        signal
        for signal in candidates
        if enabled_at is None or _ensure_utc(signal.generated_at) >= enabled_at
    ]
    summary["candidates"] = len(pending)
    if not pending:
        return summary

    providers: list[tuple[Any, Any]] = []
    for channel in config.enabled_channels:
        try:
            providers.append((channel, build_provider(channel)))
        except NotificationConfigError as exc:
            # A bad stored channel must never crash the scanner.
            logger.warning("notification channel %s is misconfigured: %s", channel.id, exc)
            summary.setdefault("channel_errors", []).append(
                {"channel": channel.id, "detail": str(exc)}
            )
    if not providers:
        return summary

    cooldown_since = (
        moment - dt.timedelta(minutes=config.cooldown_minutes)
        if config.cooldown_minutes > 0
        else None
    )
    index, sent_today = _load_recent_sends(db, cooldown_since, now=moment)

    for signal in pending:
        if config.is_quiet(moment):
            summary["skipped_quiet_hours"] += 1
            continue
        if config.cooldown_minutes > 0:
            last = index.get((signal.strategy_version_id, signal.asset_id))
            if last is not None and (moment - last).total_seconds() < config.cooldown_minutes * 60:
                summary["skipped_cooldown"] += 1
                continue
        if config.daily_max and sent_today >= config.daily_max:
            # Do not retry a dropped alert forever: mark it handled and record why.
            signal.notified_at = moment
            _audit(db, EVENT_SUPPRESSED, signal, symbol=None, detail="daily_max_reached")
            db.commit()  # per-signal commit: the decision is durable immediately
            summary["suppressed"] += 1
            continue

        # Claim before anything is sent: the receipt is written and committed while
        # this runner holds the row, so neither a concurrent runner nor a worker
        # killed here can produce a second message (docs/33 §7.3). Everything below
        # runs *after* the claim, which is why the branches that decide "not sent"
        # have to release it explicitly.
        claimed = _claim_signal(db, signal.id, moment)
        if claimed is None:
            summary["skipped_claimed"] += 1
            continue
        signal = claimed

        try:
            message = build_signal_payload(db, signal, config)
        except Exception as exc:  # noqa: BLE001 - a notification must never kill the worker
            logger.exception("could not build notification for signal %s", signal.id)
            # Nothing reached a human, so the receipt is taken back rather than
            # suppressing a signal that was never delivered.
            _release_signal(db, signal.id)
            _audit(db, EVENT_FAILED, signal, symbol=None, detail=type(exc).__name__)
            db.commit()
            summary["failed"] += 1
            continue

        symbol = message.meta.get("symbol")
        sent_channels: list[str] = []
        failures: list[tuple[str, str]] = []
        for channel, provider in providers:
            try:
                provider.send(message.title, message.body, meta=message.meta)
                sent_channels.append(channel.id)
            except (NotificationError, NotificationConfigError) as exc:
                failures.append((channel.id, str(exc)))
            except Exception as exc:  # noqa: BLE001 - one bad channel must not stop the rest
                logger.exception("channel %s failed for signal %s", channel.id, signal.id)
                failures.append((channel.id, type(exc).__name__))

        if sent_channels:
            # The event is considered surfaced once at least one channel took it;
            # a channel that failed while another succeeded is not retried (it is
            # audited instead) so no successful channel can be duplicated. The
            # receipt was already written by the claim above -- this branch only
            # records what happened.
            _audit(db, EVENT_NOTIFIED, signal, symbol=symbol, channels=sent_channels)
            for channel_id, detail in failures:
                _audit(db, EVENT_FAILED, signal, symbol=symbol, channel=channel_id, detail=detail)
            db.commit()
            index[(signal.strategy_version_id, signal.asset_id)] = moment
            sent_today += 1
            summary["sent"] += 1
        else:
            # No channel took it, so no message exists that a retry could
            # duplicate: give the signal back for the next tick.
            _release_signal(db, signal.id)
            for channel_id, detail in failures:
                _audit(db, EVENT_FAILED, signal, symbol=symbol, channel=channel_id, detail=detail)
            db.commit()
            summary["failed"] += 1

    return summary


def send_test_notification(db: Session) -> dict[str, Any]:
    """Send one test message through every enabled channel."""

    config = get_notification_config(db)
    if not config.configured:
        return {"ok": False, "detail": "通知未启用或未配置任何渠道", "results": []}

    link = f"{config.base_url.rstrip('/')}/" if config.base_url else "/"
    message = NotificationMessage(
        title="[TEST] My Quant Lab 通知测试",
        body=f"这是一条测试通知。{DISCLAIMER}",
        meta={"event": "test", "link": link},
    )

    results: list[dict[str, Any]] = []
    for channel in config.enabled_channels:
        try:
            provider = build_provider(channel)
            provider.send(message.title, message.body, meta=message.meta)
            results.append(
                {"channel": channel.id, "type": channel.type, "ok": True, "detail": "已发送"}
            )
        except (NotificationError, NotificationConfigError) as exc:
            results.append(
                {"channel": channel.id, "type": channel.type, "ok": False, "detail": str(exc)}
            )

    ok = any(item["ok"] for item in results)
    record_audit(
        db,
        event_type="notification_test",
        entity_type="notification",
        entity_id="channels",
        action="test",
        payload={"results": [{"channel": r["channel"], "ok": r["ok"]} for r in results]},
    )
    db.commit()
    if ok:
        return {"ok": True, "detail": "至少一个渠道已发送测试通知", "results": results}
    return {"ok": False, "detail": "所有渠道均发送失败", "results": results}
