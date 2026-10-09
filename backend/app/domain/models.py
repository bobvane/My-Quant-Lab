"""SQLAlchemy ORM models.

Mapping of the V1 data model (``docs/11_DATA_MODEL.md``):

* assets, market_data_sources, market_data (series), market_data_bars
* strategies, strategy_versions, strategy_parameters
* feature_snapshots
* backtest_runs, backtest_results, backtest_metrics, backtest_trades
* strategy_experiments, experiment_results
* paper_accounts, paper_positions, paper_orders, paper_trades
* ai_providers, ai_models, ai_prompts, ai_tasks, ai_usage
* audit_logs, system_settings
* signals, signal_outcomes, github_sources, github_snapshots

Invariants enforced here:

* ``strategy_versions`` rows are immutable (guarded by the service layer and by the
  database triggers in ``app/domain/immutability.py``, which every database created
  from these models gets — see ADR-094).
* every backtest run records strategy version + dataset + parameters + engine
  version + feature version so results stay reproducible.
* timestamps are stored as timezone aware UTC.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Importing the module registers the ``after_create`` listener that installs the
# immutability triggers on every database built from these models (ADR-094).
from app.domain import immutability as _immutability  # noqa: F401

UTC = dt.UTC

# SQLite only auto-increments INTEGER primary keys; PostgreSQL keeps BIGINT so
# the monitor tables can grow past 2^31 rows on a long-lived NAS.
_BigIntegerPK = BigInteger().with_variant(Integer, "sqlite")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        onupdate=_now,
        server_default=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------- #
# 1. Assets and market data
# --------------------------------------------------------------------------- #
class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False, default="stock")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    exchange: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        CheckConstraint(
            "asset_class in ('stock','etf','crypto','index','future')",
            name="ck_assets_asset_class",
        ),
    )

    series: Mapped[list[MarketDataSeries]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class MarketDataSource(Base, TimestampMixin):
    __tablename__ = "market_data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), default="rest_api", nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    series: Mapped[list[MarketDataSeries]] = relationship(back_populates="source")


class MarketDataSeries(Base, TimestampMixin):
    """A symbol+timeframe+provider stream. Versioned through ``dataset_version``."""

    __tablename__ = "market_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("market_data_sources.id"))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    adjusted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    series_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    series_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    quality_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="series")
    source: Mapped[MarketDataSource] = relationship(back_populates="series")
    bars: Mapped[list[MarketDataBar]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "timeframe",
            "source_id",
            "dataset_version",
            name="uq_market_data_series",
        ),
        Index("ix_market_data_asset_timeframe", "asset_id", "timeframe"),
    )


class MarketDataBar(Base):
    """A single OHLCV candle.

    ``market_data`` is designed so the bar table can later be moved to a
    TimescaleDB hypertable without changing the application contract: the primary
    key is ``(series_id, timestamp)``.
    """

    __tablename__ = "market_data_bars"

    series_id: Mapped[int] = mapped_column(
        ForeignKey("market_data.id", ondelete="CASCADE"), primary_key=True
    )
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 8), default=0, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    series: Mapped[MarketDataSeries] = relationship(back_populates="bars")

    __table_args__ = (
        CheckConstraint("high >= low", name="ck_bars_high_ge_low"),
        CheckConstraint("high >= open", name="ck_bars_high_ge_open"),
        CheckConstraint("high >= close", name="ck_bars_high_ge_close"),
        CheckConstraint("low <= open", name="ck_bars_low_le_open"),
        CheckConstraint("low <= close", name="ck_bars_low_le_close"),
        Index("ix_market_data_bars_ts", "timestamp"),
    )


# --------------------------------------------------------------------------- #
# 2. Strategies
# --------------------------------------------------------------------------- #
class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(24), default="custom", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(512))
    license: Mapped[str | None] = mapped_column(String(128))
    author: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(24), default="imported", nullable=False)

    versions: Mapped[list[StrategyVersion]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan", order_by="StrategyVersion.id"
    )


class StrategyVersion(Base):
    """Immutable strategy definition snapshot."""

    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(8), default="1.0", nullable=False)
    dsl_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_commit: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(512))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    immutable_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), default="pending")
    validation_errors: Mapped[list[Any] | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    strategy: Mapped[Strategy] = relationship(back_populates="versions")
    parameters: Mapped[list[StrategyParameter]] = relationship(
        back_populates="strategy_version", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)


class StrategyParameter(Base):
    __tablename__ = "strategy_parameters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    strategy_version: Mapped[StrategyVersion] = relationship(back_populates="parameters")


# --------------------------------------------------------------------------- #
# 3. Features
# --------------------------------------------------------------------------- #
class FeatureSnapshot(Base):
    """Persisted feature values for one bar, used as signal/backtest evidence."""

    __tablename__ = "feature_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("market_data.id", ondelete="CASCADE"), nullable=False
    )
    bar_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(16), nullable=False)
    values_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "series_id", "bar_timestamp", "feature_version", name="uq_feature_snapshot"
        ),
    )


# --------------------------------------------------------------------------- #
# 4. Backtests
# --------------------------------------------------------------------------- #
class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=False
    )
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("market_data.id"), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), default="1.0.0", nullable=False)
    feature_version: Mapped[str] = mapped_column(String(16), default="1.0.0", nullable=False)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_model_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # Progress is a percentage plus the human-readable step the run is on, so a
    # client that only has the run id can say "running -- computing metrics"
    # instead of a bare spinner (ADR-180). A synchronous run walks the same
    # steps and finishes on them; an asynchronous one is polled.
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    result: Mapped[BacktestResult | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )
    trades: Mapped[list[BacktestTrade]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        # A run's trades are read by things that depend on their order: the detail
        # endpoint and the trades CSV list them, the AI explanation reads the first 20,
        # and Monte Carlo resamples their P&L by index. A plain SELECT promises no order
        # at all, and PostgreSQL is free to hand back heap order, so the relationship
        # pins it (ADR-197).
        order_by="BacktestTrade.id",
    )
    # Which series the run was made on. BacktestRun stores only the id; reading a run's
    # symbol/timeframe needs the series (and its asset), so the relationship is eager to
    # keep the list endpoint from issuing a query per row.
    dataset: Mapped[MarketDataSeries | None] = relationship(
        foreign_keys=[dataset_version_id], lazy="joined"
    )

    __table_args__ = (
        Index("ix_backtest_runs_strategy", "strategy_version_id"),
        Index("ix_backtest_runs_status", "status"),
    )


class BacktestResult(Base):
    """Immutable result payload of a completed run."""

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    equity_curve_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # What the engine wanted the caller to know: parameters it had to ignore, a warm-up
    # longer than the data. Stored with the immutable result (ADR-054) because the create
    # response is not the only way to read a run -- ``GET /backtests/{id}`` used to answer
    # an empty list no matter what the run had reported, which made the warning vanish the
    # moment the page was reloaded.
    warnings_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    run: Mapped[BacktestRun] = relationship(back_populates="result")
    metrics: Mapped[list[BacktestMetric]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class BacktestMetric(Base):
    __tablename__ = "backtest_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_result_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_results.id", ondelete="CASCADE"), nullable=False
    )
    metric_group: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    metric_text: Mapped[str | None] = mapped_column(String(64))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    result: Mapped[BacktestResult] = relationship(back_populates="metrics")

    __table_args__ = (
        UniqueConstraint(
            "backtest_result_id", "metric_group", "metric_name", name="uq_backtest_metric"
        ),
    )


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    slippage: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    mae: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    mfe: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    entry_reason: Mapped[str | None] = mapped_column(String(128))
    exit_reason: Mapped[str | None] = mapped_column(String(128))
    ambiguous_fill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)

    run: Mapped[BacktestRun] = relationship(back_populates="trades")

    __table_args__ = (Index("ix_backtest_trades_run", "backtest_run_id"),)


# --------------------------------------------------------------------------- #
# 4b. Strategy experiments
# --------------------------------------------------------------------------- #
class StrategyExperiment(Base):
    """A persisted research experiment.

    An experiment is the durable record of one research run: which strategy
    version and dataset it used, the validated request that produced it, the
    parameter configuration, and -- through ``ExperimentResult`` -- every
    per-point result. The HTTP response is only a view onto this row: re-reading
    a finished experiment must not require re-running any quant code.
    """

    __tablename__ = "strategy_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=False
    )
    series_id: Mapped[int | None] = mapped_column(ForeignKey("market_data.id"))
    symbol: Mapped[str | None] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(16), default="1d", nullable=False)
    # The parameter configuration the experiment actually ran, and the validated
    # original request. Both are stored so a stored experiment can be reproduced
    # or replayed without guessing what the caller meant.
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Human-facing summary plus the primary result metrics. Nullable because a
    # failed experiment has no summary to show.
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Housekeeping timestamps: when the row was last touched, and when it left the
    # active history. Both nullable -- a row created before the lifecycle columns
    # existed never gets a backfilled value it could not have had.
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The capital and the window the experiment ran on. These are SNAPSHOT columns:
    # they are written once, from the validated request, and never re-derived from
    # the strategy's current parameters -- so re-reading an old experiment keeps
    # showing the numbers it actually ran with.
    initial_capital: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    start_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list[ExperimentResult]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_strategy_experiments_status", "status", "created_at"),
        Index("ix_strategy_experiments_version", "strategy_version_id", "created_at"),
    )


class ExperimentResult(Base):
    """One measured point of an experiment.

    For a parameter sweep this row *is* the parameter <-> result pair:
    ``parameters_json`` are the parameters that produced ``metrics_json`` and
    ``payload_json``. ``backtest_run_id`` keeps the lineage to the persisted
    ``backtest_runs`` artefact when the point produced one.
    """

    __tablename__ = "experiment_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_experiments.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))
    parameters_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    backtest_run_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_runs.id"))
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    experiment: Mapped[StrategyExperiment] = relationship(back_populates="results")

    __table_args__ = (Index("ix_experiment_results_experiment", "experiment_id", "id"),)


# --------------------------------------------------------------------------- #
# 5. Signals
# --------------------------------------------------------------------------- #
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    bar_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="FLAT", nullable=False)
    #: Which position an exit closes ("LONG"/"SHORT"); NULL when it closes
    #: nothing. A bare ``direction="FLAT"`` said only "no direction", so an exit
    #: and a non-event looked identical downstream (ADR-115).
    closes_direction: Mapped[str | None] = mapped_column(String(8))
    price_reference: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    stop_reference: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    target_reference: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    triggered_rules_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    feature_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False)
    portfolio_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    explanation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ai_task_id: Mapped[int | None] = mapped_column(ForeignKey("ai_tasks.id"))
    status: Mapped[str] = mapped_column(String(16), default="new", nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    outcome: Mapped[SignalOutcome | None] = relationship(
        back_populates="signal", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "asset_id",
            "timeframe",
            "bar_timestamp",
            name="uq_signal_event",
        ),
        Index("ix_signals_state", "state"),
    )


class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    outcome_state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    entry_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    mae: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    mfe: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    evaluated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    signal: Mapped[Signal] = relationship(back_populates="outcome")


# --------------------------------------------------------------------------- #
# 6. Paper trading (fully isolated from the real portfolio)
# --------------------------------------------------------------------------- #
class PaperAccount(Base, TimestampMixin):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"))
    # An account may be opened *from* a finished backtest. Keeping the version and
    # the exact parameter set (not just the strategy) is what makes the paper
    # result comparable with the backtest that motivated it (ADR-181). The run id
    # is nullable and never cascades: deleting a run must not delete an account.
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    backtest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="SET NULL")
    )
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    base_currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reset_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    positions: Mapped[list[PaperPosition]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    orders: Mapped[list[PaperOrder]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    trades: Mapped[list[PaperTrade]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), default=0, nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=0, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    account: Mapped[PaperAccount] = relationship(back_populates="positions")
    asset: Mapped[Asset] = relationship()

    __table_args__ = (UniqueConstraint("account_id", "asset_id", name="uq_paper_position"),)


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), default="market", nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
    filled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    slippage: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)

    account: Mapped[PaperAccount] = relationship(back_populates="orders")
    asset: Mapped[Asset] = relationship()

    __table_args__ = (CheckConstraint("side in ('BUY','SELL')", name="ck_paper_order_side"),)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    slippage: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    reason: Mapped[str | None] = mapped_column(String(128))
    strategy_version: Mapped[str | None] = mapped_column(String(64))

    account: Mapped[PaperAccount] = relationship(back_populates="trades")
    asset: Mapped[Asset] = relationship()

    __table_args__ = (Index("ix_paper_trades_account", "account_id", "entry_time"),)


# --------------------------------------------------------------------------- #
# 7. AI layer (advisory only)
# --------------------------------------------------------------------------- #
class AIProvider(Base, TimestampMixin):
    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(
        String(32), default="openai_compatible", nullable=False
    )
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    default_model: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_budget_usd: Mapped[float] = mapped_column(Numeric(12, 4), default=2.0)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    models: Mapped[list[AIModel]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class AIModel(Base, TimestampMixin):
    __tablename__ = "ai_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_tier: Mapped[str] = mapped_column(String(16), default="standard")
    input_cost_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    output_cost_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    provider: Mapped[AIProvider] = relationship(back_populates="models")

    __table_args__ = (UniqueConstraint("provider_id", "model_name", name="uq_ai_model"),)


class AIPrompt(Base, TimestampMixin):
    __tablename__ = "ai_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    task_type: Mapped[str] = mapped_column(String(48), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    capability_tier: Mapped[str] = mapped_column(String(16), default="standard")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint("name", "version", name="uq_ai_prompt"),)


class AIRoleContract(Base, TimestampMixin):
    """Runtime index of the role contract files shipped with the backend.

    The files under ``backend/app/ai/contracts/`` are the source of truth; this
    table records which versions were indexed, their content hash and the output
    schema that was in force, so an old ``ai_tasks`` row can still be explained
    after a contract changed (ADR-150).
    """

    __tablename__ = "ai_role_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(48), nullable=False)
    task_types_json: Mapped[list[Any] | None] = mapped_column(JSON)
    required_capabilities_json: Mapped[list[Any] | None] = mapped_column(JSON)
    output_language: Mapped[str | None] = mapped_column(String(16))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_path: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint("name", "version", name="uq_ai_role_contract"),)


class AITask(Base):
    __tablename__ = "ai_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(48), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("ai_providers.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id"))
    # Name snapshots taken while the task runs. Provider/model configuration is
    # deletable (ADR-177), so history must be able to name what it used without
    # the configuration row still existing.
    provider_name: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str | None] = mapped_column(String(48))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    token_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_ids_json: Mapped[list[Any] | None] = mapped_column(JSON)
    research_run_id: Mapped[int | None] = mapped_column(Integer)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_ai_tasks_type_status", "task_type", "status"),)


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("ai_providers.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id"))
    # Name snapshots; see AITask above. A deleted provider/model leaves the
    # historical name behind so cost reports stay readable.
    provider_name: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(48), nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal("0"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("usage_date", "provider_id", "model_id", "task_type", name="uq_ai_usage"),
    )


# --------------------------------------------------------------------------- #
# 7b. Research layer (Phase 3: material in, hypothesis and draft out)
# --------------------------------------------------------------------------- #
#: The research tables are a chain, not a graph: a run reads artifacts, a
#: hypothesis is read off those artifacts, and a draft formalizes one
#: hypothesis. The links back from ``ai_research_runs`` to its hypothesis and
#: draft are stored as plain integers on purpose — a database-level cycle would
#: make the migration order-dependent for no gain (ADR-154).
class ResearchArtifact(Base, TimestampMixin):
    """One piece of material a research run may read.

    The text is *not* stored: a run keeps the hashes of what it read and a few
    short excerpts, so the audit trail can say which bytes produced an answer
    without becoming a second copy of somebody else's document (ADR-153).

    Two hashes on purpose: ``source_hash`` is the material the caller handed
    over and ``text_hash`` is the version this run actually read. They differ
    whenever a source was truncated, and citations are verified against the
    second one — "the AI read this text" is a checkable claim, not a note
    (ADR-161).
    """

    __tablename__ = "research_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_research_runs.id"))
    source_ref: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    uri: Mapped[str | None] = mapped_column(String(1024))
    parse_status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    license_note: Mapped[str | None] = mapped_column(Text)
    #: The observation this material came out of, when it arrived over the network.
    #: Nullable on purpose: a caller-supplied text source never fetched anything.
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("ai_source_snapshots.id"))

    __table_args__ = (UniqueConstraint("run_id", "source_ref", name="uq_research_artifact_ref"),)


class AISourceSnapshot(Base, TimestampMixin):
    """One observation of an external source, appended and never overwritten.

    A snapshot records what the platform actually saw at one moment: the URL it was
    asked for and the URL it ended on, the HTTP metadata, the hash of the bytes that
    came back, the hash of the text read out of them, which parser produced that text
    and which retention decision followed (ADR-163). Fetching the same URL twice
    writes two rows, so ``ResearchArtifact.snapshot_id`` can later answer "which
    material did this run read?" instead of "which URL did it name?".

    The third-party full text has no column here on purpose, and the excerpt is
    capped by the retention policy in ``app/ai/research.py`` (ADR-161) — a snapshot
    is an observation, not a second copy of somebody else's document.
    """

    __tablename__ = "ai_source_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: ``url`` for a fetched page, ``pdf`` for a PDF handed over by a caller.
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    #: Where the fetch ended after redirects; differs from ``url`` when it followed one.
    final_url: Mapped[str | None] = mapped_column(String(2048))
    #: ``retained`` | ``blocked`` | ``fetch_failed``. What happened to the fetch
    #: as a whole; ``parse_status`` below answers the separate question of whether
    #: the bytes could be read at all (docs/27 §8).
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``ok`` | ``unsupported`` | ``parse_failed`` | ``not_parsed``.
    parse_status: Mapped[str] = mapped_column(String(16), default="not_parsed", nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    #: Characters the parser produced before the excerpt cap was applied, so a
    #: reader can see "read 200 KB, parsed 40 K chars, kept 500" at a glance.
    chars_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Identity of the bytes as they arrived (ADR-161). Never the AI cache identity.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    #: Identity of the text this platform read out of those bytes.
    text_hash: Mapped[str | None] = mapped_column(String(64))
    parser: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    robots_ok: Mapped[bool | None] = mapped_column(Boolean)
    retention: Mapped[str] = mapped_column(String(16), default="excerpt", nullable=False)
    retained_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    license_note: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    #: Redirect chain, page count, title, dropped headers — whatever the fetch learned.
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_ai_source_snapshots_url_time", "url", "created_at"),)


class ResearchArtifactFragment(Base):
    """A quotable piece of one artifact, with where inside it the piece sits."""

    __tablename__ = "research_artifact_fragments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("research_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    locator_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    text_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    fragment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_research_fragment_artifact", "artifact_id"),)


class AIResearchRun(Base, TimestampMixin):
    """One research request: question, material, steps and outcome."""

    __tablename__ = "ai_research_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    current_step: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    hypothesis_id: Mapped[int | None] = mapped_column(Integer)
    draft_id: Mapped[int | None] = mapped_column(Integer)
    capability_status: Mapped[str | None] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_json: Mapped[list[Any] | None] = mapped_column(JSON)
    warnings_json: Mapped[list[Any] | None] = mapped_column(JSON)
    violations_json: Mapped[list[Any] | None] = mapped_column(JSON)
    researcher_task_id: Mapped[int | None] = mapped_column(ForeignKey("ai_tasks.id"))
    architect_task_id: Mapped[int | None] = mapped_column(ForeignKey("ai_tasks.id"))
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_ai_research_runs_status", "status", "created_at"),)


class StrategyHypothesis(Base, TimestampMixin):
    """What the researcher understood, stored before anything was formalized."""

    __tablename__ = "strategy_hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_research_runs.id", ondelete="CASCADE"), nullable=False
    )
    strategy_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    understanding: Mapped[str | None] = mapped_column(Text)
    confidence_self_reported: Mapped[str | None] = mapped_column(String(16))
    role: Mapped[str | None] = mapped_column(String(48))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    provider_name: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    ai_task_id: Mapped[int | None] = mapped_column(ForeignKey("ai_tasks.id"))
    hypothesis_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_strategy_hypotheses_run", "run_id"),)


class StrategyHypothesisRule(Base):
    """One rule of a hypothesis, kept per row so its origin stays queryable."""

    __tablename__ = "strategy_hypothesis_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_hypotheses.id", ondelete="CASCADE"), nullable=False
    )
    rule_key: Mapped[str] = mapped_column(String(32), nullable=False)
    field: Mapped[str] = mapped_column(String(24), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(16))
    capability_status: Mapped[str | None] = mapped_column(String(24))
    required_capabilities_json: Mapped[list[Any] | None] = mapped_column(JSON)
    evidence_fragment_ids_json: Mapped[list[Any] | None] = mapped_column(JSON)
    parameters_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("hypothesis_id", "rule_key", name="uq_strategy_hypothesis_rule"),
    )


class StrategyDraft(Base, TimestampMixin):
    """A formalized strategy: richer than the DSL, and not executable."""

    __tablename__ = "strategy_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_research_runs.id", ondelete="CASCADE"), nullable=False
    )
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("strategy_hypotheses.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(16), default="1.0", nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    capability_status: Mapped[str] = mapped_column(String(24), nullable=False)
    #: What the model claimed about its own support, kept next to the server's
    #: verdict so an overclaim attempt is visible in the trail.
    model_status: Mapped[str | None] = mapped_column(String(24))
    executable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_experimental_of: Mapped[int | None] = mapped_column(Integer)
    compiled_strategy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id")
    )
    ai_task_id: Mapped[int | None] = mapped_column(ForeignKey("ai_tasks.id"))
    model_name: Mapped[str | None] = mapped_column(String(128))
    draft_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    capability_report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_strategy_drafts_run", "run_id"),)


# --------------------------------------------------------------------------- #
# 8. Audit and settings
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(48), nullable=False)
    # 64, not 32: "strategy_experiment_adopted_from_backtest" is 41 characters, and
    # PostgreSQL refused the write while SQLite (every local test) accepted it (ADR-186 --
    # the same too-narrow-column trap ADR-064 hit on alembic_version.version_num).
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_created", "created_at"),
    )


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), default="system")


# --------------------------------------------------------------------------- #
# 9. GitHub importer provenance
# --------------------------------------------------------------------------- #
class GitHubSource(Base, TimestampMixin):
    __tablename__ = "github_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_url: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(128), default="main", nullable=False)
    current_commit: Mapped[str | None] = mapped_column(String(64))
    license: Mapped[str | None] = mapped_column(String(128))
    author: Mapped[str | None] = mapped_column(String(128))
    is_watched: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_import_status: Mapped[str | None] = mapped_column(String(32))
    # The commit whose draft is waiting for a human. The watcher analysed it and
    # refused to import it (a draft never invents exit rules, or the version ledger
    # would not number it), so the row has to keep saying a review is outstanding
    # instead of collapsing into "unchanged" on the next beat (ADR-062).
    pending_review_commit: Mapped[str | None] = mapped_column(String(64))

    snapshots: Mapped[list[GitHubSnapshot]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class GitHubSnapshot(Base):
    __tablename__ = "github_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("github_sources.id", ondelete="CASCADE"), nullable=False
    )
    commit: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    extraction_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    source: Mapped[GitHubSource] = relationship(back_populates="snapshots")

    __table_args__ = (UniqueConstraint("source_id", "commit", name="uq_github_snapshot"),)
