"""Notification layer tests (M11).

These pin down the four promises the notification feature makes:

1. **Secrets are write-only** — the webhook URL and signing secret never come
   back from the API, only a mask plus a "is set" flag.
2. **One event, one notification** — ``notified_at`` makes delivery idempotent,
   so re-running the worker does not spam.
3. **Noise control works** — state filter, quiet hours, daily cap and cooldown.
4. **SSRF hardening** — non-HTTP schemes and cloud metadata hosts are refused.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import httpx
import pytest

from app.domain.models import (
    Asset,
    MarketDataSeries,
    MarketDataSource,
    Signal,
    Strategy,
    StrategyVersion,
)
from app.notifications.config import (
    get_notification_config,
    quiet_hours_contains,
    update_notification_config,
)
from app.notifications.provider import (
    NotificationConfigError,
    NotificationError,
    WebhookNotificationProvider,
    validate_webhook_url,
)
from app.notifications.service import build_signal_payload, notify_pending_signals

URL = "https://hooks.example.com/quantlab"


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _patch_post(monkeypatch, calls: list[dict], status: int = 200) -> None:
    def _post(  # noqa: ANN001
        url, content=None, headers=None, timeout=None, follow_redirects=None, **kwargs
    ):
        calls.append(
            {
                "url": url,
                "content": content,
                "json": kwargs.get("json"),
                "headers": headers,
                "follow_redirects": follow_redirects,
            }
        )
        return _Resp(status)

    monkeypatch.setattr(httpx, "post", _post)


def _patch_post_by_url(monkeypatch, calls: list[dict], status_fn) -> None:
    def _post(  # noqa: ANN001
        url, content=None, headers=None, timeout=None, follow_redirects=None, **kwargs
    ):
        calls.append({"url": url, "json": kwargs.get("json"), "content": content})
        return _Resp(status_fn(url))

    monkeypatch.setattr(httpx, "post", _post)


def _seed_signal(
    db_session,
    *,
    state: str = "BUY",
    symbol: str = "NQ",
    bar: dt.datetime | None = None,
    rules: list[str] | None = None,
) -> Signal:
    strategy = Strategy(name=f"S-{symbol}-{state}", slug=f"s-{symbol.lower()}-{state.lower()}")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="h" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol=symbol, asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name=f"src-{symbol}-{state}", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    signal = Signal(
        strategy_version_id=version.id,
        asset_id=asset.id,
        timeframe="1d",
        bar_timestamp=bar or dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state=state,
        direction="LONG" if state == "BUY" else "FLAT",
        price_reference=100.0,
        triggered_rules_json=rules or ["ema_cross"],
        feature_snapshot_hash="f" * 64,
        data_source=f"series:{series.id}/v1",
    )
    db_session.add(signal)
    db_session.commit()
    db_session.refresh(signal)
    return signal


def _enable(db_session, **overrides):
    changes = {"enabled": True, "webhook_url": URL}
    changes.update(overrides)
    return update_notification_config(db_session, changes)


# --------------------------------------------------------------------------- #
# Config & secrets
# --------------------------------------------------------------------------- #
def test_config_round_trip_masks_secrets(client) -> None:
    created = client.put(
        "/api/v1/notifications/config",
        json={
            "enabled": True,
            "include_wait": True,
            "daily_max": 5,
            "channels": [
                {
                    "id": "hook",
                    "type": "webhook",
                    "enabled": True,
                    "url": URL,
                    "secret": "top-secret-signing-key",
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()

    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["include_wait"] is True
    assert body["eligible_states"] == ["BUY", "SELL", "WAIT"]
    channel = body["channels"][0]
    assert channel["id"] == "hook"
    assert channel["type"] == "webhook"
    assert channel["url_set"] is True
    assert channel["secret_set"] is True
    assert channel["url_masked"].startswith("https://hooks.example.com")
    # No secret value may appear anywhere in the response.
    assert URL not in created.text
    assert "top-secret-signing-key" not in created.text

    # A second read stays masked too.
    again = client.get("/api/v1/notifications/config").json()
    assert URL not in json.dumps(again)


def test_legacy_single_webhook_still_works(client) -> None:
    created = client.put(
        "/api/v1/notifications/config",
        json={"enabled": True, "webhook_url": URL, "webhook_secret": "s3cret"},
    )
    assert created.status_code == 200, created.text
    channels = created.json()["channels"]
    assert len(channels) == 1
    assert channels[0]["type"] == "webhook"
    assert channels[0]["url_set"] is True
    assert URL not in created.text


def test_update_rejects_bad_url_and_quiet_hours(client) -> None:
    bad_scheme = client.put("/api/v1/notifications/config", json={"webhook_url": "ftp://x/y"})
    assert bad_scheme.status_code == 422

    metadata = client.put(
        "/api/v1/notifications/config",
        json={"webhook_url": "http://169.254.169.254/latest/meta-data"},
    )
    assert metadata.status_code == 422

    quiet = client.put("/api/v1/notifications/config", json={"quiet_hours": "25:00-99:00"})
    assert quiet.status_code == 422


def test_audit_records_config_change_without_secrets(client) -> None:
    client.put("/api/v1/notifications/config", json={"webhook_url": URL})
    audit = client.get("/api/v1/audit/logs").json()
    events = [e for e in audit["events"] if e["event_type"] == "notification_config_updated"]
    assert events
    assert URL not in str(audit)


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #
def test_validate_webhook_url_rules() -> None:
    assert validate_webhook_url("https://example.com/hook") == "https://example.com/hook"
    assert validate_webhook_url("http://192.168.1.10:8123/hook") == "http://192.168.1.10:8123/hook"
    assert validate_webhook_url("") == ""
    for bad in (
        "ftp://x",
        "file:///etc/passwd",
        "http://169.254.169.254/",
        "http://metadata.google.internal/",
    ):
        with pytest.raises(NotificationConfigError):
            validate_webhook_url(bad)


def test_provider_signs_body_and_never_redirects(monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=204)

    provider = WebhookNotificationProvider(URL, secret="s3cr3t")
    assert provider.send("t", "b", meta={"symbol": "NQ"}) is True

    assert len(calls) == 1
    assert calls[0]["url"] == URL
    # A webhook must not be able to bounce us to an internal address.
    assert calls[0]["follow_redirects"] is False
    expected = "sha256=" + hmac.new(b"s3cr3t", calls[0]["content"], hashlib.sha256).hexdigest()
    assert calls[0]["headers"]["X-QuantLab-Signature"] == expected
    assert json.loads(calls[0]["content"])["symbol"] == "NQ"


def test_provider_raises_on_non_2xx(monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=500)
    provider = WebhookNotificationProvider(URL)
    with pytest.raises(NotificationError):
        provider.send("t", "b")


# --------------------------------------------------------------------------- #
# Payload contract
# --------------------------------------------------------------------------- #
def test_signal_payload_has_required_contract_fields(db_session) -> None:
    _enable(db_session)
    signal = _seed_signal(db_session, symbol="AAPL")
    message = build_signal_payload(db_session, signal, get_notification_config(db_session))

    for field in ("symbol", "signal", "strategy", "timeframe", "timestamp", "reason", "link"):
        assert field in message.meta, field
    assert message.meta["symbol"] == "AAPL"
    assert message.meta["signal"] == "BUY"
    assert message.meta["strategy"]["version"] == "1.0.0"
    # Discipline: the disclaimer travels with every payload.
    assert "不构成投资建议" in message.meta["disclaimer"]


# --------------------------------------------------------------------------- #
# Delivery & noise control
# --------------------------------------------------------------------------- #
def test_notify_sends_once_and_dedups(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session)
    _seed_signal(db_session)

    first = notify_pending_signals(db_session)
    assert first["sent"] == 1
    assert len(calls) == 1

    # Re-running must not re-send the same event.
    second = notify_pending_signals(db_session)
    assert second["sent"] == 0
    assert second["candidates"] == 0
    assert len(calls) == 1


def test_wait_is_excluded_by_default_and_included_when_asked(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session)
    _seed_signal(db_session, state="WAIT", symbol="WAITX")

    assert notify_pending_signals(db_session)["sent"] == 0

    _enable(db_session, include_wait=True)
    assert notify_pending_signals(db_session)["sent"] == 1


def test_quiet_hours_postpones_without_marking_notified(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session, quiet_hours="00:00-06:00")
    signal = _seed_signal(db_session)

    quiet_moment = dt.datetime(2026, 1, 2, 3, 0, tzinfo=dt.UTC)
    assert notify_pending_signals(db_session, now=quiet_moment)["skipped_quiet_hours"] == 1
    db_session.refresh(signal)
    assert signal.notified_at is None

    # After the window closes the same signal is delivered.
    loud_moment = dt.datetime(2026, 1, 2, 9, 0, tzinfo=dt.UTC)
    assert notify_pending_signals(db_session, now=loud_moment)["sent"] == 1


def test_daily_max_suppresses_and_marks_handled(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session, daily_max=1)
    _seed_signal(db_session, symbol="AAA")
    second = _seed_signal(db_session, symbol="BBB", bar=dt.datetime(2026, 1, 3, tzinfo=dt.UTC))

    result = notify_pending_signals(db_session)
    assert result["sent"] == 1
    assert result["suppressed"] == 1
    db_session.refresh(second)
    assert second.notified_at is not None  # not retried forever


def _seed_two_signals_same_series(db_session) -> tuple[Signal, Signal]:
    """Two signals sharing (strategy version, asset) on different bars."""

    strategy = Strategy(name="Cooldown", slug="cooldown")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="c" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="COOL", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="src-cooldown", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    signals = []
    for day in (2, 4):
        signal = Signal(
            strategy_version_id=version.id,
            asset_id=asset.id,
            timeframe="1d",
            bar_timestamp=dt.datetime(2026, 1, day, tzinfo=dt.UTC),
            state="BUY",
            direction="LONG",
            price_reference=100.0,
            triggered_rules_json=["ema_cross"],
            feature_snapshot_hash="f" * 64,
            data_source=f"series:{series.id}/v1",
        )
        db_session.add(signal)
        signals.append(signal)
    db_session.commit()
    for signal in signals:
        db_session.refresh(signal)
    return signals[0], signals[1]


def test_daily_max_counts_across_runs(db_session, monkeypatch) -> None:
    """A zero cooldown must not silently disable the daily cap."""

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session, daily_max=1, cooldown_minutes=0)
    _seed_signal(db_session, symbol="R1")
    assert notify_pending_signals(db_session)["sent"] == 1

    _seed_signal(db_session, symbol="R2", bar=dt.datetime(2026, 1, 3, tzinfo=dt.UTC))
    second_run = notify_pending_signals(db_session)
    assert second_run["sent"] == 0
    assert second_run["suppressed"] == 1


def test_cooldown_skips_second_signal_for_same_series(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session, cooldown_minutes=60)
    _first, second = _seed_two_signals_same_series(db_session)

    result = notify_pending_signals(db_session)
    assert result["sent"] == 1
    assert result["skipped_cooldown"] == 1
    db_session.refresh(second)
    assert second.notified_at is None  # still eligible once the cooldown passes


def test_failure_leaves_signal_pending_for_retry(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=503)
    _enable(db_session)
    signal = _seed_signal(db_session)

    result = notify_pending_signals(db_session)
    assert result["failed"] == 1
    assert result["sent"] == 0
    db_session.refresh(signal)
    assert signal.notified_at is None


def test_notify_disabled_is_a_noop(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    # Not enabled / no URL.
    _seed_signal(db_session)
    result = notify_pending_signals(db_session)
    assert result["enabled"] is False
    assert calls == []


# --------------------------------------------------------------------------- #
# Quiet-hours parsing
# --------------------------------------------------------------------------- #
def test_quiet_hours_contains_crossing_midnight() -> None:
    at = lambda h, m=0: dt.datetime(2026, 1, 2, h, m, tzinfo=dt.UTC)  # noqa: E731
    assert quiet_hours_contains("22:00-07:00", at(23)) is True
    assert quiet_hours_contains("22:00-07:00", at(3)) is True
    assert quiet_hours_contains("22:00-07:00", at(12)) is False
    assert quiet_hours_contains("09:00-17:00", at(10)) is True
    assert quiet_hours_contains("09:00-17:00", at(20)) is False
    # An unparseable window never suppresses alerts.
    assert quiet_hours_contains("garbage", at(3)) is False


# --------------------------------------------------------------------------- #
# Test endpoint
# --------------------------------------------------------------------------- #
def test_test_endpoint_reports_success_and_failure(client, monkeypatch) -> None:
    client.put("/api/v1/notifications/config", json={"enabled": True, "webhook_url": URL})

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    ok = client.post("/api/v1/notifications/test")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    _patch_post(monkeypatch, calls, status=500)
    failed = client.post("/api/v1/notifications/test")
    assert failed.status_code == 200
    assert failed.json()["ok"] is False


def test_test_endpoint_requires_configuration(client) -> None:
    response = client.post("/api/v1/notifications/test")
    assert response.status_code == 200
    assert response.json()["ok"] is False


# --------------------------------------------------------------------------- #
# Hardening: encryption at rest, no backlog dump, bypass prevention, no rows
# --------------------------------------------------------------------------- #
def test_secrets_are_encrypted_at_rest(client, db_session) -> None:
    from app.domain.models import SystemSetting

    client.put(
        "/api/v1/notifications/config",
        json={"enabled": True, "webhook_url": URL, "webhook_secret": "sign-me"},
    )
    stored = {
        row.key: str(row.value_json)
        for row in db_session.query(SystemSetting).all()
        if row.key.startswith("notification_")
    }
    assert URL not in stored.get("notification_webhook_url", "")
    assert "sign-me" not in stored.get("notification_webhook_secret", "")


def test_enabling_does_not_dump_backlog(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    # A signal that predates the feature being enabled.
    _seed_signal(db_session, symbol="OLD")
    _enable(db_session)

    assert notify_pending_signals(db_session)["sent"] == 0
    assert calls == []

    # A signal generated after enabling is delivered.
    _seed_signal(db_session, symbol="NEW", bar=dt.datetime(2026, 1, 5, tzinfo=dt.UTC))
    assert notify_pending_signals(db_session)["sent"] == 1


def test_generic_settings_cannot_write_notification_keys(client) -> None:
    response = client.put(
        "/api/v1/settings",
        json={"key": "notification_webhook_url", "value": "http://10.0.0.1/hook"},
    )
    assert response.status_code == 400
    assert "notifications/config" in response.json()["detail"]


def test_scheduled_scan_skips_no_signal_rows(db_session) -> None:
    """NO_SIGNAL bars must not be persisted (no per-bar table growth)."""

    import numpy as np
    import pandas as pd

    from app.data.market_data_repo import frame_to_bars, upsert_bars
    from app.domain.models import MarketDataSeries, MarketDataSource
    from app.simulation.signal_engine import scan_and_persist

    dsl = {
        "schema_version": "1.0",
        "strategy": {"id": "flat", "name": "Flat", "version": "1.0.0"},
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
        "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
        "execution": {"fill_model": "next_bar_open", "fee_bps": 0, "slippage_bps": 0},
    }
    strategy = Strategy(name="Flat", slug="flat-scan")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json=dsl, immutable_hash="z" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol="FLAT", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name="flat-src", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()

    periods = 120
    index = pd.date_range("2024-01-01", periods=periods, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": np.full(periods, 100.0),
            "high": np.full(periods, 100.0),
            "low": np.full(periods, 100.0),
            "close": np.full(periods, 100.0),
            "volume": np.full(periods, 1000.0),
        },
        index=index,
    ).rename_axis("timestamp")
    upsert_bars(db_session, series, frame_to_bars(frame))
    db_session.commit()

    result = scan_and_persist(db_session)
    assert result["created"] == 0
    assert db_session.query(Signal).count() == 0


# --------------------------------------------------------------------------- #
# P1 channels: feishu / telegram / pushplus / email
# --------------------------------------------------------------------------- #
def test_feishu_payload_and_signature(monkeypatch) -> None:
    from app.notifications.providers import FeishuProvider

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    provider = FeishuProvider("https://open.feishu.cn/open-apis/bot/v2/hook/abc", secret="key")
    assert provider.send("NQ BUY", "rules matched", meta={"symbol": "NQ"}) is True

    payload = calls[0]["json"]
    assert payload["msg_type"] == "text"
    assert "NQ BUY" in payload["content"]["text"]
    assert payload["sign"]  # signed when a secret is configured
    assert "key" not in json.dumps(calls[0])


def test_telegram_targets_bot_api(monkeypatch) -> None:
    from app.notifications.providers import TelegramProvider

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    provider = TelegramProvider("123:ABC", "-100999")
    provider.send("t", "b")

    assert calls[0]["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert calls[0]["json"]["chat_id"] == "-100999"
    assert "t" in calls[0]["json"]["text"]


def test_pushplus_payload(monkeypatch) -> None:
    from app.notifications.providers import PushPlusProvider

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    provider = PushPlusProvider("pp-token", topic="lab")
    provider.send("t", "b")

    assert calls[0]["url"] == "https://www.pushplus.plus/send"
    assert calls[0]["json"]["token"] == "pp-token"
    assert calls[0]["json"]["topic"] == "lab"
    assert calls[0]["json"]["template"] == "txt"


def test_email_uses_smtp(monkeypatch) -> None:
    import smtplib

    from app.notifications.providers import EmailProvider

    sent: list = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None) -> None:  # noqa: ANN001
            self.host = host
            self.port = port
            self.started_tls = False
            self.logged_in = None

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def starttls(self, context=None) -> None:  # noqa: ANN001
            self.started_tls = True

        def login(self, username, password) -> None:  # noqa: ANN001
            self.logged_in = (username, password)

        def send_message(self, message) -> None:  # noqa: ANN001
            sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    provider = EmailProvider(
        "smtp.example.com", "ops@example.com", username="bot", password="pw", port=587
    )
    assert provider.send("signal", "body", meta={"link": "https://lab/"}) is True
    assert sent[0]["Subject"] == "signal"
    assert sent[0]["To"] == "ops@example.com"
    assert "https://lab/" in sent[0].get_content()


def test_smtp_credentials_require_encryption() -> None:
    from app.notifications.providers import EmailProvider

    with pytest.raises(NotificationConfigError):
        EmailProvider(
            "smtp.example.com", "a@b.com", username="u", password="p", use_tls=False, use_ssl=False
        )


def test_email_metadata_and_link_local_host_rejected(client) -> None:
    for host in ("169.254.169.254", "metadata.google.internal"):
        response = client.put(
            "/api/v1/notifications/config",
            json={
                "channels": [
                    {
                        "id": "mail",
                        "type": "email",
                        "enabled": True,
                        "host": host,
                        "to_address": "ops@example.com",
                    }
                ]
            },
        )
        assert response.status_code == 422, host


def test_email_port_must_be_a_valid_integer() -> None:
    from app.notifications.channels import normalize_channel

    with pytest.raises(NotificationConfigError):
        normalize_channel(
            {
                "type": "email",
                "enabled": True,
                "host": "smtp.example.com",
                "to_address": "ops@example.com",
                "port": "abc",
            }
        )


def test_channel_field_length_is_capped(client) -> None:
    response = client.put(
        "/api/v1/notifications/config",
        json={"channels": [{"id": "pp", "type": "pushplus", "enabled": True, "token": "x" * 5000}]},
    )
    assert response.status_code == 422


def test_channel_secrets_are_encrypted_at_rest(client, db_session) -> None:
    from app.domain.models import SystemSetting

    client.put(
        "/api/v1/notifications/config",
        json={
            "channels": [
                {
                    "id": "tg",
                    "type": "telegram",
                    "enabled": True,
                    "bot_token": "123456:SECRET",
                    "chat_id": "9",
                }
            ]
        },
    )
    row = db_session.query(SystemSetting).filter(SystemSetting.key == "notification_channels").one()
    stored = str(row.value_json)
    assert "123456:SECRET" not in stored
    # `...set` flags are exposed, the value itself is not.
    body = client.get("/api/v1/notifications/config").json()
    assert body["channels"][0]["bot_token_set"] is True
    assert "123456:SECRET" not in client.get("/api/v1/notifications/config").text


def test_enabled_channel_with_missing_fields_rejected(client) -> None:
    response = client.put(
        "/api/v1/notifications/config",
        json={"channels": [{"type": "telegram", "enabled": True, "bot_token": "", "chat_id": ""}]},
    )
    assert response.status_code == 422


def test_duplicate_channel_ids_rejected(client) -> None:
    response = client.put(
        "/api/v1/notifications/config",
        json={
            "channels": [
                {"id": "dup", "type": "pushplus", "enabled": False},
                {"id": "dup", "type": "pushplus", "enabled": False},
            ]
        },
    )
    assert response.status_code == 422


def test_omitted_secret_keeps_stored_value(client) -> None:
    client.put(
        "/api/v1/notifications/config",
        json={
            "channels": [
                {
                    "id": "tg",
                    "type": "telegram",
                    "enabled": True,
                    "bot_token": "tok-1",
                    "chat_id": "5",
                }
            ]
        },
    )
    updated = client.put(
        "/api/v1/notifications/config",
        json={"channels": [{"id": "tg", "type": "telegram", "enabled": True, "chat_id": "6"}]},
    )
    assert updated.status_code == 200
    channel = updated.json()["channels"][0]
    assert channel["chat_id"] == "6"
    assert channel["bot_token_set"] is True  # kept


def test_multi_channel_routing_sends_to_all(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    from app.notifications.config import update_notification_config

    update_notification_config(
        db_session,
        {
            "enabled": True,
            "channels": [
                {"id": "hook", "type": "webhook", "enabled": True, "url": URL},
                {
                    "id": "tg",
                    "type": "telegram",
                    "enabled": True,
                    "bot_token": "1:A",
                    "chat_id": "9",
                },
            ],
        },
    )
    _seed_signal(db_session)
    result = notify_pending_signals(db_session)
    assert result["sent"] == 1
    assert len(calls) == 2
    urls = {call["url"] for call in calls}
    assert URL in urls
    assert any("api.telegram.org" in url for url in urls)


def test_partial_channel_failure_still_marks_signal_sent(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post_by_url(monkeypatch, calls, lambda url: 500 if "telegram" in url else 200)
    from app.notifications.config import update_notification_config

    update_notification_config(
        db_session,
        {
            "enabled": True,
            "channels": [
                {"id": "hook", "type": "webhook", "enabled": True, "url": URL},
                {
                    "id": "tg",
                    "type": "telegram",
                    "enabled": True,
                    "bot_token": "1:A",
                    "chat_id": "9",
                },
            ],
        },
    )
    _seed_signal(db_session)
    result = notify_pending_signals(db_session)
    assert result["sent"] == 1

    from app.domain.models import AuditLog

    failed = [
        r for r in db_session.query(AuditLog).all() if r.event_type == "signal_notification_failed"
    ]
    assert failed and failed[0].payload_json["channel"] == "tg"


def test_all_channels_failing_leaves_signal_pending(db_session, monkeypatch) -> None:
    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=503)
    from app.notifications.config import update_notification_config

    update_notification_config(
        db_session,
        {
            "enabled": True,
            "channels": [
                {"id": "hook", "type": "webhook", "enabled": True, "url": URL},
                {
                    "id": "tg",
                    "type": "telegram",
                    "enabled": True,
                    "bot_token": "1:A",
                    "chat_id": "9",
                },
            ],
        },
    )
    signal = _seed_signal(db_session)
    result = notify_pending_signals(db_session)
    assert result["failed"] == 1
    assert result["sent"] == 0
    db_session.refresh(signal)
    assert signal.notified_at is None
