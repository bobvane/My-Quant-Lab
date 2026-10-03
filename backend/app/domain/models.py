"""SQLAlchemy ORM models.

Mapping of the V1 data model (``docs/11_DATA_MODEL.md``):

* assets, market_data_sources, market_data (series), market_data_bars
* strategies, strategy_versions, strategy_parameters
* feature_snapshots
* backtest_runs, backtest_results, backtest_metrics, backtest_trades
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
        back_populates="run", cascade="all, delete-orphan"
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


class AITask(Base):
    __tablename__ = "ai_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(48), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("ai_providers.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id"))
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    token_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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
# 8. Audit and settings
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
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


# --------------------------------------------------------------------------- #
# 10. System Resource Monitor (docs/20_RESOURCE_MONITOR.md)
# --------------------------------------------------------------------------- #
class HostResourceSample(Base):
    """One NAS-wide sample per collection cycle (default: every 60s)."""

    __tablename__ = "host_resource_samples"

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    cpu_percent: Mapped[float | None] = mapped_column(Numeric(6, 2))
    cpu_count: Mapped[int | None] = mapped_column(Integer)
    mem_used_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    mem_total_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    swap_used_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    swap_total_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    disk_used_gb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    disk_total_gb: Mapped[float | None] = mapped_column(Numeric(14, 2))


class ContainerResourceSample(Base):
    """One container's stats per collection cycle.

    ``is_quantlab`` comes from the compose project label, so topology changes
    (6 containers -> 4 -> 3) never require touching this table.
    """

    __tablename__ = "container_resource_samples"

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    container_name: Mapped[str] = mapped_column(String(128), nullable=False)
    compose_service: Mapped[str | None] = mapped_column(String(96))
    is_quantlab: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cpu_percent: Mapped[float | None] = mapped_column(Numeric(8, 2))
    mem_used_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    mem_limit_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    state: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (Index("ix_cont_res_name_ts", "container_name", "ts"),)


class ResourceRollup(Base):
    """Aggregated bucket ('5m') used for 7d/30d charts.

    Raw samples only cover RESOURCE_RETENTION_RAW_DAYS; charts beyond that read
    this table. Recomputed from raw data at each collection boundary, so there
    is no incremental-average drift.
    """

    __tablename__ = "resource_rollups"

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    granularity: Mapped[str] = mapped_column(String(8), nullable=False)  # '5m'
    bucket_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(96), nullable=False)  # 'host'/'quantlab'/name
    cpu_avg: Mapped[float | None] = mapped_column(Numeric(8, 2))
    cpu_max: Mapped[float | None] = mapped_column(Numeric(8, 2))
    mem_avg_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    mem_max_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))

    __table_args__ = (
        UniqueConstraint("granularity", "bucket_start", "scope", name="uq_resource_rollup"),
        Index("ix_resource_rollups_bucket", "granularity", "bucket_start"),
    )


class ResourceEvent(Base):
    """Task resource events (e.g. a backtest) with window peaks.

    Peaks are read from the sample tables over the event's time window; when a
    task is shorter than one collection cycle the peaks stay null rather than
    being invented.
    """

    __tablename__ = "resource_events"

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    cpu_peak_percent: Mapped[float | None] = mapped_column(Numeric(8, 2))
    mem_peak_mb: Mapped[float | None] = mapped_column(Numeric(14, 2))
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
