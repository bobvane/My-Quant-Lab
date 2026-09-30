"""Pydantic schemas for the V1 API."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AIStatusOut",
    "AITaskOut",
    "AssetCreate",
    "AssetOut",
    "BacktestCreate",
    "BacktestOut",
    "BacktestSummaryOut",
    "BarOut",
    "ExplainOut",
    "GithubAnalyzeRequest",
    "GithubAnalyzeOut",
    "GithubImportRequest",
    "HealthOut",
    "MarketDataSyncRequest",
    "PaperAccountCreate",
    "PaperAccountOut",
    "SignalOut",
    "StrategyCreate",
    "StrategyOut",
    "StrategyValidationOut",
    "StrategyVersionCreate",
    "StrategyVersionOut",
    "SystemInfoOut",
    "WalkForwardRequest",
    "WalkForwardOut",
]


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    redis: str
    workers: str
    environment: str
    feature_version: str
    engine_version: str


class SystemInfoOut(BaseModel):
    app_name: str
    version: str
    environment: str
    market_data_provider: str
    default_currency: str
    default_timezone: str
    feature_version: str
    engine_version: str
    strategy_schema_version: str
    modules: list[str]


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    display_name: str | None = None
    asset_class: str = Field(default="stock", pattern="^(stock|etf|crypto|index|future)$")
    currency: str = Field(default="USD", min_length=3, max_length=8)
    exchange: str | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    display_name: str | None
    asset_class: str
    currency: str
    exchange: str | None
    is_active: bool


class BarOut(BaseModel):
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


class MarketDataSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str = "1d"
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    lookback_days: int = Field(default=400, ge=1, le=20_000)
    provider: str | None = None


class StrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    slug: str | None = Field(default=None, max_length=128)
    description: str | None = None
    source_type: str = "custom"
    source_url: str | None = None
    license: str | None = None
    author: str | None = None


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None
    source_type: str
    source_url: str | None
    license: str | None
    author: str | None
    status: str
    lifecycle: str
    created_at: dt.datetime
    version_count: int = 0


class StrategyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=32)
    dsl: dict[str, Any]
    source_commit: str | None = None
    source_url: str | None = None
    prompt_version: str | None = None
    evidence: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    make_current: bool = True


class StrategyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    version: str
    schema_version: str
    immutable_hash: str
    validation_status: str
    is_current: bool
    created_at: dt.datetime
    dsl: dict[str, Any]


class StrategyValidationOut(BaseModel):
    is_valid: bool
    issues: list[dict[str, Any]]
    available_columns: list[str]


class BacktestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: int
    series_id: int | None = None
    symbol: str | None = None
    timeframe: str = "1d"
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution_overrides: dict[str, Any] = Field(default_factory=dict)


class BacktestSummaryOut(BaseModel):
    id: int
    strategy_version_id: int
    dataset_version_id: int
    engine_version: str
    feature_version: str
    status: str
    dataset_hash: str
    created_at: dt.datetime
    total_return: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None
    win_rate: float | None = None
    number_of_trades: int | None = None
    final_equity: float | None = None


class BacktestOut(BacktestSummaryOut):
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    result_hash: str
    parameters: dict[str, Any]
    execution_model: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class WalkForwardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: int
    symbol: str | None = None
    timeframe: str = "1d"
    train_bars: int = Field(default=250, ge=60)
    test_bars: int = Field(default=60, ge=20)
    step: int | None = Field(default=None, ge=1)


class WalkForwardOut(BaseModel):
    windows: int
    train_bars: int
    test_bars: int
    segments: list[dict[str, Any]]
    summary: dict[str, Any]


class PaperAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    initial_cash: float = Field(default=100_000.0, gt=0)
    base_currency: str = "USD"
    strategy_id: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class PaperAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    strategy_id: int | None
    base_currency: str
    initial_cash: float
    cash: float
    status: str
    reset_count: int
    created_at: dt.datetime


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_version_id: int
    asset_id: int
    timeframe: str
    bar_timestamp: dt.datetime
    state: str
    direction: str
    price_reference: float | None
    stop_reference: float | None
    target_reference: float | None
    triggered_rules: list[Any] = Field(default_factory=list)
    status: str
    generated_at: dt.datetime
    explanation: dict[str, Any] | None = None


class GithubAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_url: str = Field(min_length=10, max_length=512)
    ref: str | None = Field(default=None, max_length=128)
    token: str | None = Field(
        default=None,
        max_length=512,
        description="Optional GitHub token to raise API rate limits; never stored",
    )
    max_files: int = Field(default=12, ge=1, le=30, description="Cap on fetched files (.py first)")


class GithubAnalyzeOut(BaseModel):
    owner: str
    repo: str
    ref: str
    description: str | None = None
    license: str | None = None
    files_scanned: list[str]
    files_skipped: list[str]
    indicators: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    params: list[dict[str, Any]]
    unknowns: list[dict[str, Any]]
    unsafe_flags: list[dict[str, Any]]
    draft_dsl: dict[str, Any]
    warnings: list[str]


class GithubImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_url: str = Field(min_length=10, max_length=512)
    ref: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1.0.0", min_length=1, max_length=32)
    dsl: dict[str, Any]


class AIStatusOut(BaseModel):
    configured: bool
    provider_name: str | None = None
    model: str | None = None
    daily_budget_usd: float | None = None
    spent_today_usd: float = 0.0
    tasks_today: int = 0
    note: str


class ExplainOut(BaseModel):
    explanation: dict[str, Any]
    cached: bool = False
    task_id: int | None = None
    model: str | None = None
    cost_usd_estimated: float = 0.0


class AITaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    prompt_name: str
    prompt_version: str
    status: str
    cost_usd: float | None = None
    created_at: dt.datetime
    completed_at: dt.datetime | None = None
    error_message: str | None = None
