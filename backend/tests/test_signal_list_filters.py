"""Signal listing filters (ADR-181).

A paper account is bound to one strategy version, so it needs that version's
signals. Filtering client-side over the newest N rows silently loses them once
other strategies are generating signals too, so the filter lives on the query.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Asset, Signal, Strategy, StrategyVersion

_BAR = datetime(2024, 1, 15, tzinfo=UTC)


def _version(db, name: str) -> int:
    strategy = Strategy(name=name, slug=f"filt-{name}")
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version="1.0.0",
        dsl_json={"schema_version": "1.0"},
        immutable_hash="f" * 64,
        validation_status="valid",
        is_current=True,
    )
    db.add(version)
    db.flush()
    return version.id


def _signal(db, *, version_id: int, asset_id: int, state: str) -> None:
    db.add(
        Signal(
            strategy_version_id=version_id,
            asset_id=asset_id,
            timeframe="1d",
            bar_timestamp=_BAR,
            state=state,
            direction="LONG",
            closes_direction=None,
            triggered_rules_json=["filt_rule"],
            feature_snapshot_hash="f" * 64,
            data_source="series:1/v1",
        )
    )
    db.flush()


def test_the_signal_list_can_be_narrowed_to_one_strategy_version(client, db_session) -> None:
    asset = Asset(symbol="FILT", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    version_a = _version(db_session, "a")
    version_b = _version(db_session, "b")
    _signal(db_session, version_id=version_a, asset_id=asset.id, state="BUY")
    _signal(db_session, version_id=version_b, asset_id=asset.id, state="WAIT")
    db_session.commit()

    everything = client.get("/api/v1/signals").json()
    assert {row["strategy_version_id"] for row in everything} == {version_a, version_b}

    narrowed = client.get(f"/api/v1/signals?strategy_version_id={version_a}")
    assert narrowed.status_code == 200
    assert [row["strategy_version_id"] for row in narrowed.json()] == [version_a]
    assert narrowed.json()[0]["state"] == "BUY"

    missing = client.get("/api/v1/signals?strategy_version_id=999999")
    assert missing.status_code == 200
    assert missing.json() == []
