"""The immutability invariants must hold in every database, not just the migrated one.

``0002_immutability`` installed the two guards only when the dialect was PostgreSQL, and
only inside that migration. Every database built by ``Base.metadata.create_all()`` — the
unit suite, the probe scripts, a developer's scratch database — had no guard at all,
which is precisely why the ``operator does not exist: json = json`` defect
(``0003_fix_triggers_json``) could only be discovered by a release smoke test: the only
database a developer builds locally could not run the guard.

The guards now live in ``app/domain/immutability.py`` and are installed by
``Base.metadata``'s ``after_create`` event, so the SQLite suite below exercises the same
rules the PostgreSQL suite exercises — mutating a published strategy version or a
completed result is refused, while the routine ``is_current`` flip keeps working.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from app.data.strategy_service import create_strategy_version
from app.domain.models import (
    Asset,
    BacktestResult,
    BacktestRun,
    MarketDataSeries,
    MarketDataSource,
    Strategy,
    StrategyVersion,
)

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "imm", "name": "Immutable", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "gt", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "lt", "left": "close", "right": "ema20"}]}},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"

# The guarded columns, and the value that would count as a rewrite.
MUTATIONS = {
    "dsl_json": '{"tampered": true}',
    "version": "9.9.9",
    "immutable_hash": "f" * 64,
}


def _strategy_version(db_session, name: str = "Imm") -> StrategyVersion:
    strategy = Strategy(name=name, slug=name.lower().replace(" ", "-"))
    db_session.add(strategy)
    db_session.flush()
    version = create_strategy_version(db_session, strategy, version="1.0.0", dsl=DSL)
    db_session.commit()
    return version


def _completed_result(db_session) -> BacktestResult:
    strategy = Strategy(name="Imm Res", slug="imm-res")
    db_session.add(strategy)
    db_session.flush()
    version = create_strategy_version(db_session, strategy, version="1.0.0", dsl=DSL)
    asset = Asset(symbol="IMM", asset_class="stock")
    source = MarketDataSource(name="imm-src", base_url="x")
    db_session.add_all([asset, source])
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        dataset_hash="a" * 64,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    result = BacktestResult(
        backtest_run_id=run.id,
        summary_json={"total_return": 0.1},
        metrics_json={},
        equity_curve_json=[],
        result_hash="b" * 64,
    )
    db_session.add(result)
    db_session.commit()
    return result


def test_the_test_database_really_carries_the_guards(db_session) -> None:
    """Behaviour first: the create_all database is the one that used to be unguarded."""

    names = {
        row[0]
        for row in db_session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        ).all()
    }

    assert {"trg_strategy_versions_immutable", "trg_backtest_results_immutable"} <= names


@pytest.mark.parametrize("column", sorted(MUTATIONS))
def test_a_published_strategy_version_cannot_be_rewritten(db_session, column: str) -> None:
    version = _strategy_version(db_session)
    statement = text(f"UPDATE strategy_versions SET {column} = :value WHERE id = :id")  # noqa: S608

    with pytest.raises(IntegrityError, match="immutable"):
        db_session.execute(statement, {"value": MUTATIONS[column], "id": version.id})
    db_session.rollback()


def test_the_current_version_flag_can_still_be_flipped(db_session) -> None:
    """The regression that made every strategy-version UPDATE a 500 must stay fixed."""

    version = _strategy_version(db_session)

    db_session.execute(
        update(StrategyVersion).where(StrategyVersion.id == version.id).values(is_current=False)
    )
    db_session.commit()

    assert db_session.get(StrategyVersion, version.id).is_current is False


def test_a_completed_result_cannot_be_rewritten(db_session) -> None:
    result = _completed_result(db_session)

    with pytest.raises(IntegrityError, match="immutable"):
        db_session.execute(
            update(BacktestResult)
            .where(BacktestResult.id == result.id)
            .values(summary_json={"total_return": 999.0})
        )
    db_session.rollback()


def test_the_guard_protects_the_result_and_not_the_run(db_session) -> None:
    """The run is mutable state (archiving, status changes); only the result is sealed."""

    result = _completed_result(db_session)

    db_session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == result.backtest_run_id)
        .values(status="archived")
    )
    db_session.commit()

    assert db_session.get(BacktestRun, result.backtest_run_id).status == "archived"


def test_the_guards_have_exactly_one_definition_in_app() -> None:
    """A second copy of the DDL is how the two databases drifted apart in the first place."""

    owners = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in APP_DIR.rglob("*.py")
        if "CREATE TRIGGER" in path.read_text(encoding="utf-8")
    )

    assert owners == ["backend/app/domain/immutability.py"]


def test_the_migration_chain_uses_the_shared_definition() -> None:
    migration = (REPO_ROOT / "backend/alembic/versions/0010_immutability.py").read_text(
        encoding="utf-8"
    )

    assert "from app.domain.immutability import install_immutability_triggers" in migration
    assert "install_immutability_triggers(op.get_bind())" in migration


def test_the_models_register_the_guards() -> None:
    models = (REPO_ROOT / "backend/app/domain/models.py").read_text(encoding="utf-8")

    assert "from app.domain import immutability" in models
