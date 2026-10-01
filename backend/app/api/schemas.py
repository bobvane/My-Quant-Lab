"""Pydantic schemas for the V1 API."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AIStatusOut",
    "AITaskOut",
    "AIModelIn",
    "AIProviderCreate",
    "AIProviderOut",
    "AIProviderTestOut",
    "AIProviderTestRequest",
    "AIProviderUpdate",
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
    "LifecycleApplyIn",
    "LifecycleApplyOut",
    "LifecycleStageOut",
    "MarketDataSyncRequest",
    "NotificationConfigOut",
    "NotificationConfigUpdate",
    "NotificationTestOut",
    "OOSOut",
    "OOSRequest",
    "PaperAccountCreate",
    "PaperAccountOut",
    "PaperExecuteRequest",
    "PaperExecutionOut",
    "PaperFundRequest",
    "PaperOrderOut",
    "PaperPositionOut",
    "SignalOut",
    "StrategyCreate",
    "StrategyLifecycleOut",
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


class OOSRequest(BaseModel):
    """Single train/test split for out-of-sample validation (docs/07 §11)."""

    model_config = ConfigDict(extra="forbid")

    strategy_version_id: int
    symbol: str | None = None
    timeframe: str = "1d"
    oos_pct: float | None = Field(default=0.2, gt=0.0, lt=1.0)
    oos_start: str | None = None


class OOSOut(BaseModel):
    split_time: str
    in_sample_bars: int
    out_of_sample_bars: int
    in_sample: dict[str, Any]
    out_of_sample: dict[str, Any]


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


class PaperExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: int = Field(ge=1)
    fee_bps: float | None = Field(default=None, ge=0, le=1000)
    slippage_bps: float | None = Field(default=None, ge=0, le=1000)
    max_position_pct: float | None = Field(default=None, gt=0, le=1)


class PaperFundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Positive adds virtual cash, negative withdraws it.
    amount: float = Field(description="Signed virtual cash adjustment")


class PaperPositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    asset_id: int
    quantity: float
    avg_cost: float
    realized_pnl: float


class PaperOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    signal_id: int | None = None
    asset_id: int
    side: str
    status: str
    quantity: float
    fill_price: float | None = None
    fees: float
    slippage: float
    reason: str | None = None
    created_at: dt.datetime
    filled_at: dt.datetime | None = None


class PaperExecutionOut(BaseModel):
    account_id: int
    side: str
    order_id: int
    quantity: float
    fill_price: float
    fees: float
    slippage: float
    realized_pnl: float
    cash: float


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


class AIModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(min_length=1, max_length=128)
    capability_tier: str = Field(default="standard", pattern="^(cheap|standard|high)$")
    input_cost_per_mtok: float = Field(default=0.0, ge=0)
    output_cost_per_mtok: float = Field(default=0.0, ge=0)


class AIProviderCreate(BaseModel):
    """Create an AI provider.

    The API key is write-only: it is encrypted at rest and never returned.
    ``base_url`` must point at an OpenAI-compatible ``/chat/completions``
    endpoint, which covers OpenAI, DeepSeek, Qwen, Kimi, GLM, OpenRouter,
    Gemini-compatible gateways and local vLLM alike.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    provider_type: str = Field(
        default="openai_compatible", pattern="^(openai_compatible|anthropic|google)$"
    )
    base_url: str = Field(min_length=8, max_length=512)
    api_key: str = Field(min_length=4, max_length=512)
    default_model: str | None = Field(default=None, max_length=128)
    daily_budget_usd: float = Field(default=2.0, ge=0, le=1000)
    is_active: bool = True
    models: list[AIModelIn] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _require_https(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith(("https://", "http://")):
            raise ValueError("base_url must start with https:// (or http:// for a local endpoint)")
        return text


class AIProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=8, max_length=512)
    # Omit to keep the stored key; send an empty string to clear it.
    api_key: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, max_length=128)
    daily_budget_usd: float | None = Field(default=None, ge=0, le=1000)
    is_active: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _require_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().rstrip("/")
        if not text.startswith(("https://", "http://")):
            raise ValueError("base_url must start with https:// (or http:// for a local endpoint)")
        return text


class AIProviderOut(BaseModel):
    id: int
    name: str
    provider_type: str
    base_url: str
    default_model: str | None
    is_active: bool
    daily_budget_usd: float
    api_key_set: bool
    key_masked: str
    models: list[dict[str, Any]] = Field(default_factory=list)


class AIProviderTestOut(BaseModel):
    ok: bool
    detail: str
    models_found: list[str] = Field(default_factory=list)


class AIProviderTestRequest(BaseModel):
    """Test connectivity before saving, or test an already stored provider."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=8, max_length=512)
    api_key: str = Field(min_length=4, max_length=512)


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


class NotificationConfigOut(BaseModel):
    """Notification settings. Channel secrets are only ever exposed masked."""

    enabled: bool
    configured: bool
    include_wait: bool
    quiet_hours: str
    daily_max: int
    cooldown_minutes: int
    base_url: str
    enabled_at: dt.datetime | None = None
    eligible_states: list[str] = Field(default_factory=list)
    channels: list[dict[str, Any]] = Field(default_factory=list)


class NotificationConfigUpdate(BaseModel):
    """Partial update: only the provided fields are changed.

    Sending ``channels`` replaces the whole list. Within a channel, omit a
    secret field to keep the stored value, or send an empty string to clear it.
    ``webhook_url`` / ``webhook_secret`` remain as a legacy single-channel
    shorthand.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    include_wait: bool | None = None
    quiet_hours: str | None = Field(default=None, max_length=32)
    daily_max: int | None = Field(default=None, ge=0, le=10_000)
    cooldown_minutes: int | None = Field(default=None, ge=0, le=10_080)
    base_url: str | None = Field(default=None, max_length=512)
    channels: list[dict[str, Any]] | None = None
    webhook_url: str | None = Field(default=None, max_length=1024)
    webhook_secret: str | None = Field(default=None, max_length=512)


class NotificationTestOut(BaseModel):
    ok: bool
    detail: str
    results: list[dict[str, Any]] = Field(default_factory=list)


class LifecycleStageOut(BaseModel):
    stage: str
    group: str
    reached: bool


class StrategyLifecycleOut(BaseModel):
    """Current stage plus the deterministic evidence behind the next step."""

    strategy_id: int
    name: str
    current: str
    current_group: str
    suggested_next: str | None = None
    blocked_reason: str | None = None
    reference_eligible: bool = False
    degraded: bool = False
    degrade_reason: str | None = None
    stages: list[LifecycleStageOut] = Field(default_factory=list)
    gates: dict[str, bool] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    manual_only_stages: list[str] = Field(default_factory=list)


class LifecycleApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_stage: str = Field(min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=512)


class LifecycleApplyOut(BaseModel):
    strategy_id: int
    previous: str
    current: str
    applied: bool
    detail: str
