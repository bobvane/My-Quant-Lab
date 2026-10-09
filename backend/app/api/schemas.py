"""Pydantic schemas for the V1 API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AIStatusOut",
    "AITaskOut",
    "AIModelIn",
    "AIProviderCreate",
    "AIProviderOut",
    "AIProviderTestOut",
    "AIProviderTestRequest",
    "AIProviderUpdate",
    "AnalysisBenchmarkOut",
    "AnalysisCaveatOut",
    "AnalysisDerivedOut",
    "AnalysisOut",
    "AnalysisPerformanceOut",
    "AnalysisRiskOut",
    "AnalysisSampleOut",
    "AnalysisWindowOut",
    "AnalysisWorstTradeOut",
    "AssetCreate",
    "AssetOut",
    "BacktestCreate",
    "BacktestOut",
    "BacktestSummaryOut",
    "BarOut",
    "ExperimentAdoptRequest",
    "ExperimentCompareOut",
    "ExperimentCreate",
    "ExperimentDetailOut",
    "ExperimentListOut",
    "ExperimentResultOut",
    "ExperimentSummaryOut",
    "ExperimentUpdate",
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
    # Which series/timeframe the run was made on. `dataset_version_id` alone is an
    # opaque id; without these a caller cannot tell whether two runs are even
    # comparable (the ensemble comparison table reads them to say so), and cannot
    # trace a number back to the dataset it came from (ADR-119).
    symbol: str | None = None
    timeframe: str | None = None
    dataset_version: str | None = None
    source: str | None = None
    # Where the run is, so a client that only holds an id can say "running — computing
    # metrics" instead of showing a spinner. `progress` is 0-100 and `current_step` names
    # the rung that percentage belongs to (ADR-180); `error_message` is what a failed run
    # has instead of a result, and is null while it is still running.
    progress: int = 0
    current_step: str | None = None
    error_message: str | None = None


class BacktestOut(BacktestSummaryOut):
    # Every result field is optional because a run may legitimately have no result yet: with
    # BACKTEST_ASYNC on, POST /backtests answers while the engine is still to run, and
    # GET /backtests/{id} answers a poll for that same run. They are *absent*, never empty —
    # an empty equity curve would read as a flat backtest (ADR-180).
    metrics: dict[str, Any] | None = None
    equity_curve: list[dict[str, Any]] | None = None
    trades: list[dict[str, Any]] | None = None
    result_hash: str | None = None
    parameters: dict[str, Any] | None = None
    execution_model: dict[str, Any] | None = None
    warnings: list[str] | None = Field(default_factory=list)


class AnalysisWindowOut(BaseModel):
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    bars: int = 0


class AnalysisDerivedOut(BaseModel):
    """Numbers Phase C derives from the stored curve, kept apart from the engine's own."""

    calmar: float | None = None
    downside_deviation: float | None = None
    excess_return: float | None = None
    final_equity_gap: float | None = None
    worst_bar_return: float | None = None


class AnalysisPerformanceOut(BaseModel):
    # `stored` is the engine's metric block verbatim; `derived` is everything Phase C
    # adds. Keeping them apart is what lets a reader tell a measured number from a
    # number computed afterwards (ADR-188).
    stored: dict[str, Any] = Field(default_factory=dict)
    derived: AnalysisDerivedOut


class AnalysisWorstTradeOut(BaseModel):
    pnl: float | None = None
    exit_time: dt.datetime | None = None
    direction: str | None = None


class AnalysisRiskOut(BaseModel):
    max_drawdown: float | None = None
    max_drawdown_duration_bars: int | None = None
    max_drawdown_duration_days: float | None = None
    recovery_bars: int | None = None
    recovered: bool | None = None
    recovery_text: str | None = None
    worst_bar_return: float | None = None
    worst_month_return: float | None = None
    worst_trade: AnalysisWorstTradeOut | None = None
    max_consecutive_losses: int | None = None
    downside_deviation: float | None = None


class AnalysisCurvePointOut(BaseModel):
    """One bar of the comparison, in the same money as the strategy's own curve."""

    timestamp: dt.datetime | None = None
    equity: float


class AnalysisBenchmarkOut(BaseModel):
    """The buy-and-hold comparison. Named `benchmark` in the payload, 「对照」 in the UI.

    「基准」 already means the deposit denominator in this project (ADR-066), so the
    comparison is never called that.
    """

    label: str
    kind: str
    source: str
    fees_included: bool = False
    window_matched: bool
    bars_matched: int
    # The drawable comparison path, so the browser never has to rebuild it from
    # the closes (ADR-188: one computation path, in the analysis layer).
    curve: list[AnalysisCurvePointOut] = Field(default_factory=list)
    total_return: float | None = None
    cagr: float | None = None
    annualized_volatility: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    final_equity: float | None = None


class AnalysisSampleOut(BaseModel):
    trades: int
    bars: int
    years: float | None = None
    tier: Literal["insufficient", "preliminary", "enough"]
    tier_text: str


class AnalysisCaveatOut(BaseModel):
    code: str
    message: str


class AnalysisOut(BaseModel):
    """One read-only view over an immutable result: performance, risk, comparison."""

    run_id: int
    result_hash: str | None = None
    analysis_version: str
    window: AnalysisWindowOut
    performance: AnalysisPerformanceOut
    risk: AnalysisRiskOut
    benchmark: AnalysisBenchmarkOut | None = None
    sample: AnalysisSampleOut
    caveats: list[AnalysisCaveatOut] = Field(default_factory=list)


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
    # A window whose test segment never left the strategy's warm-up was not measured:
    # its out-of-sample return is the flat 0.0 of a strategy that never traded, so it
    # stays out of ``summary`` and is counted here instead (ADR-055 / ADR-196).
    measured_oos_windows: int = 0
    unmeasured_oos_windows: int = 0
    warnings: list[str] = Field(default_factory=list)
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


class SensitivityRequest(BaseModel):
    """Sweep a strategy's declared parameters over a grid (docs/21, ADR-040).

    ``grid`` maps a parameter name (one the strategy declares) to the values to
    evaluate. Every combination is backtested independently, so the cost is
    ``len(grid points)`` backtests; the engine caps that at
    ``MAX_GRID_POINTS`` and answers 422 beyond it.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_version_id: int
    symbol: str | None = None
    timeframe: str = "1d"
    grid: dict[str, list[Any]] = Field(min_length=1)
    base_parameters: dict[str, Any] | None = None
    metric: str = "sharpe"


class SensitivityOut(BaseModel):
    sensitivity_version: str
    metric: str
    axes: dict[str, list[Any]]
    grid_points: int
    evaluated_points: int
    # Points that were actually measured, and therefore the only ones the ranking, the
    # summary statistics and the stability verdict are computed from. A point whose whole
    # window sat inside the strategy's warm-up is counted in ``evaluated_points`` (its
    # objective is defined -- it is the flat 0.0 of a strategy that never traded) but not
    # here (v1.4.5 / ADR-055).
    ranked_points: int
    warmup_unmet_points: int
    warnings: list[str] = Field(default_factory=list)
    points: list[dict[str, Any]]
    summary: dict[str, Any]
    best: dict[str, Any] | None = None
    worst: dict[str, Any] | None = None
    stable: bool | None = None


class MonteCarloRequest(BaseModel):
    """Resample a completed backtest's trades (docs/22, ADR-043).

    Uses the trades already stored for ``backtest_run_id`` — no new backtest is run.
    """

    model_config = ConfigDict(extra="forbid")

    backtest_run_id: int
    runs: int = Field(default=1000, ge=1, le=5000)
    trades_per_run: int | None = Field(default=None, ge=1)
    seed: int = 0


class MonteCarloOut(BaseModel):
    monte_carlo_version: str
    seed: int
    timeframe: str
    method: str
    summary: dict[str, Any]
    sample_equity_paths: list[list[float]]
    warnings: list[str]


class ExperimentCreate(BaseModel):
    """One persisted research experiment (docs/25, ADR-174).

    ``kind`` selects which existing engine runs and what the kind-specific fields mean;
    the fields another kind would have used are accepted but inert (they are echoed back
    in the stored request, so the history shows exactly what was sent).

    Every kind-specific requirement is enforced before a row is written -- a rejected
    request answers 422 and stores nothing -- so the ranges below match the shapes the
    sibling research endpoints already accept, rather than defining a second contract for
    the same engine. ``extra="forbid"`` keeps a typo such as ``runs_count`` from silently
    becoming a default-valued experiment.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["backtest", "sensitivity", "monte_carlo", "walk_forward", "oos"]
    strategy_version_id: int
    notes: str | None = None
    symbol: str | None = None
    series_id: int | None = None
    timeframe: str = "1d"
    start: str | None = None
    end: str | None = None
    parameters: dict[str, Any] | None = None
    grid: dict[str, list[Any]] | None = None
    metric: str = "sharpe"
    backtest_run_id: int | None = None
    runs: int = Field(default=1000, ge=1, le=5000)
    trades_per_run: int | None = Field(default=None, ge=1)
    seed: int = 0
    train_bars: int = Field(default=250, ge=60)
    test_bars: int = Field(default=60, ge=20)
    step: int | None = Field(default=None, ge=1)
    oos_pct: float | None = Field(default=0.2, gt=0.0, lt=1.0)
    oos_start: str | None = None
    # A draft is the same validated request, stored without running anything: the row
    # freezes what will be executed and the engine runs on ``POST /experiments/{id}/run``.
    draft: bool = False


class ExperimentUpdate(BaseModel):
    """PATCH body for the two human-facing fields of a stored experiment.

    Each field is individually optional, but a PATCH that changes neither is a client
    mistake rather than a no-op: it answers 422 instead of silently touching the row.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _require_one_field(self) -> ExperimentUpdate:
        if self.name is None and self.notes is None:
            raise ValueError("give at least one of name or notes")
        return self


class ExperimentAdoptRequest(BaseModel):
    """Optional body of ``POST /experiments/from-backtest/{run_id}``.

    The run already carries the numbers; this only lets the caller name the experiment
    it becomes. Omit the body entirely to accept the generated default name.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class ExperimentResultOut(BaseModel):
    """One stored result row: a grid point, or the single payload of another kind.

    ``parameters`` is the parameter set this exact result was produced with, which is what
    makes a sweep re-readable as parameter/result pairs instead of a flat list of scores.
    """

    id: int
    kind: str
    label: str | None = None
    parameters: dict[str, Any] | None = None
    backtest_run_id: int | None = None
    metrics: dict[str, Any] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime


class ExperimentSummaryOut(BaseModel):
    """List/history shape: no result rows and no engine payloads."""

    id: int
    name: str
    kind: str
    status: str
    strategy_version_id: int
    series_id: int | None = None
    symbol: str | None = None
    timeframe: str
    result_count: int = 0
    backtest_run_id: int | None = None
    # The comparable metrics (`EXPERIMENT_METRICS`, ADR-185), flattened out of the stored
    # summary so a history row is readable without loading the experiment. Stored, never
    # recomputed; a metric the engine did not store stays missing/None rather than 0.
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    error_message: str | None = None
    # Lifecycle: when the row was last touched, and when it was archived (if ever).
    updated_at: dt.datetime | None = None
    archived_at: dt.datetime | None = None
    # The run configuration frozen at creation. ``symbols`` is the instrument list the
    # experiment actually touched, and ``is_adopted`` marks a row that came from a stored
    # backtest instead of from running an engine here.
    initial_capital: float | None = None
    start_date: dt.datetime | None = None
    end_date: dt.datetime | None = None
    strategy_id: int | None = None
    strategy_name: str | None = None
    version: str | None = None
    symbols: list[str] = Field(default_factory=list)
    is_adopted: bool = False


class ExperimentDetailOut(ExperimentSummaryOut):
    notes: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] | None = None
    results: list[ExperimentResultOut] = Field(default_factory=list)


class ExperimentListOut(BaseModel):
    experiments: list[ExperimentSummaryOut] = Field(default_factory=list)


class ExperimentCompareOut(BaseModel):
    metrics: list[str]
    # Whether the compared rows ran the same configuration, and -- when they did not --
    # which of the stored dimensions differ. Both come from comparing the STORED
    # configuration server-side; nothing is guessed from the response shape.
    comparability: Literal["same-config", "different-config"] = "different-config"
    differences: list[str] = Field(default_factory=list)
    experiments: list[dict[str, Any]]


class EnsembleMemberIn(BaseModel):
    """One strategy version taking part in an ensemble vote (docs/24)."""

    model_config = ConfigDict(extra="forbid")

    strategy_version_id: int
    weight: float = Field(default=1.0, ge=0)


class EnsembleRequest(BaseModel):
    """Vote several strategy versions into one portfolio (docs/24, ADR-047)."""

    model_config = ConfigDict(extra="forbid")

    members: list[EnsembleMemberIn] = Field(min_length=1)
    symbol: str | None = None
    timeframe: str = "1d"
    # The vote must strictly exceed this; two equal members therefore need both to
    # agree (each contributes exactly 0.5). Valid range is [0, 1).
    vote_threshold: float = Field(default=0.5, ge=0.0, lt=1.0)
    execution_overrides: dict[str, Any] = Field(default_factory=dict)


class EnsembleOut(BaseModel):
    ensemble_version: str
    vote_threshold: float
    members: list[dict[str, Any]]
    # Each member run on the ensemble's own bars with the ensemble's own cost model.
    # A member's stored backtest may have used a different window or fee model, which
    # made the old comparison table incomparable; these are the same-bar numbers.
    member_runs: list[dict[str, Any]] = Field(default_factory=list)
    bars_evaluated: int
    agreement: dict[str, Any]
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    final_equity: float
    initial_capital: float
    warnings: list[str]
    # Which dataset the vote ran on, and how the engine labelled itself. Callers need
    # the id to check whether a member's own stored backtest is even comparable; the
    # versions were previously computed and then silently dropped by this model.
    dataset_version_id: int | None = None
    symbol: str | None = None
    timeframe: str = "1d"
    engine_version: str
    feature_version: str


class EnsembleSweepRequest(EnsembleRequest):
    """Vote the same members at several thresholds (docs/24 §7, ADR-052).

    ``vote_threshold`` from the base request is ignored here; each entry of
    ``thresholds`` is evaluated instead. Omitting ``thresholds`` uses the thresholds
    where the answer can change: the member weight-share coalition totals.
    """

    thresholds: list[float] | None = Field(default=None, min_length=1)
    """Explicit thresholds, each in ``[0, 1)``. The engine caps how many it will run."""


class EnsembleSweepOut(BaseModel):
    ensemble_version: str
    engine_version: str
    feature_version: str
    bars_evaluated: int
    initial_capital: float
    thresholds: list[float]
    points: list[dict[str, Any]]
    members: list[dict[str, Any]]
    # Every distinct total the weighted vote can take. Between two of these values
    # nothing can change, which is why the sweep's surface is a staircase.
    possible_votes: list[float]
    # The number of thresholds a sweep will evaluate. A caller-supplied list over this
    # length is a 422, and so is a default grid that would need more points than this.
    max_thresholds: int
    warnings: list[str]
    dataset_version_id: int | None = None
    symbol: str | None = None
    timeframe: str = "1d"


class PaperAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    initial_cash: float = Field(default=100_000.0, gt=0)
    base_currency: str = "USD"
    strategy_id: int | None = None
    # The binding: which *version* (and which exact parameters) the paper result
    # belongs to. A strategy id alone cannot answer that, so an account opened from a
    # backtest keeps both (ADR-181). Omitted fields are copied from the run.
    strategy_version_id: int | None = None
    backtest_run_id: int | None = None
    parameters: dict[str, Any] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class PaperAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    name: str
    strategy_id: int | None
    strategy_version_id: int | None = None
    backtest_run_id: int | None = None
    # Validated from the ORM column `parameters_json`, published as `parameters`.
    parameters: dict[str, Any] = Field(default_factory=dict, validation_alias="parameters_json")
    # Resolved server-side so the panel does not need a second request per account.
    strategy_name: str | None = None
    base_currency: str
    # The DB column is still called `initial_cash`, but funding moves it in both
    # directions, so what it holds is the account's net deposits (ADR-066).
    net_deposits: float = Field(validation_alias="initial_cash")
    cash: float
    # Realized P&L of the closed trades, so a caller can name a profit without calling
    # `cash - net_deposits`, which is only the same number while nothing is open: a
    # full-size buy spends the cash and would read as -100% (ADR-124).
    realized_pnl: float = 0.0
    # Marked-to-market totals of the OPEN positions, from paper data only: the cash is
    # paper cash and the mark is the close of the latest *closed* bar, so nothing here
    # reaches the real portfolio (ADR-006). `null` while no bar qualifies to mark with,
    # because an invented price would be worse than a named gap (ADR-007).
    market_value: float | None = None
    unrealized_pnl: float | None = None
    total_equity: float | None = None
    total_pnl: float | None = None
    # A ratio, not a percentage: `0.05` means +5% (ADR-087). `null` while net deposits
    # are <= 0, where a ratio would have no denominator — `metric_notes` says so.
    total_pnl_pct: float | None = None
    metric_notes: list[str] = Field(default_factory=list)
    status: str
    reset_count: int
    created_at: dt.datetime


class PaperExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: int = Field(ge=1)
    # Explicit sizing: `quantity` is units, `notional` is the gross trade value in the
    # account currency. Both are optional and mutually exclusive; without them the
    # engine keeps its all-in sizing, so old callers are unaffected (ADR-181).
    quantity: Decimal | None = Field(
        default=None, gt=0, description="Exact units to trade; mutually exclusive with `notional`"
    )
    notional: Decimal | None = Field(
        default=None,
        gt=0,
        description="Gross trade value; mutually exclusive with `quantity`",
    )
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
    symbol: str | None = None
    quantity: float
    avg_cost: float
    realized_pnl: float
    # The mark is the close of the latest *closed* bar for the position's asset, read
    # through the one market-data loader (ADR-119). When no bar qualifies the mark
    # fields stay `null` and `mark_note` says why: a position without a mark is a gap
    # to name, never a price to invent (ADR-007, ADR-023).
    mark_price: float | None = None
    mark_time: dt.datetime | None = None
    mark_note: str | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    # A ratio, not a percentage: `0.05` means +5% (ADR-087).
    unrealized_pnl_pct: float | None = None


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
    symbol: str | None = None
    strategy_name: str | None = None
    strategy_version: str | None = None
    timeframe: str
    bar_timestamp: dt.datetime
    state: str
    direction: str
    #: Set when the signal closes a position ("LONG"/"SHORT"); null otherwise.
    closes_direction: str | None = None
    price_reference: float | None
    stop_reference: float | None
    target_reference: float | None
    triggered_rules: list[Any] = Field(default_factory=list)
    portfolio_context: dict[str, Any] | None = None
    status: str
    generated_at: dt.datetime
    notified_at: dt.datetime | None = None
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
    max_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Wall-clock budget for the whole fetch; the rest is reported unread",
    )


class GithubAnalyzeOut(BaseModel):
    owner: str
    repo: str
    ref: str
    # The commit ``ref`` pointed at when the fetch started. The report describes
    # this revision, and the import records it (ADR-060).
    commit: str
    description: str | None = None
    license: str | None = None
    # ``coverage`` is the review surface: it says how many candidate files the
    # repository had, how many were downloaded, and how many were only
    # inventoried or not read at all (docs/05 section 4.1).
    analysis_version: str
    coverage: dict[str, Any]
    files_parsed: list[str]
    files_inventoried: list[str]
    files_skipped: list[dict[str, Any]]
    # Downloaded but not understood: a Python file whose parse failed contributed
    # nothing, so it must not be listed as parsed (ADR-059).
    files_unparsed: list[dict[str, Any]]
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
    # The commit the reviewed analysis read, taken from ``GithubAnalyzeOut.commit``.
    # It is required: a strategy version has to name the revision it came from, and
    # a branch name is not a revision (ADR-060).
    commit: str = Field(
        min_length=7,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{7,64}$",
        description="Commit SHA the reviewed analysis read; stored as source_commit",
    )
    name: str = Field(min_length=1, max_length=128)
    # Omitted means "the service that owns the version ledger picks the next free
    # one"; a default of `1.0.0` was a guess that collided with the first import
    # of the same strategy every time (ADR-061).
    version: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="Explicit strategy version; omit to let the server assign the next one",
    )
    dsl: dict[str, Any]


class GithubVersionPlanOut(BaseModel):
    """What a name resolves to before an import: the ledger and the next version."""

    name: str
    slug: str
    strategy_id: int | None = None
    versions: list[str]
    next_version: str | None = None
    can_assign: bool
    reason: str = ""


class AIStatusOut(BaseModel):
    configured: bool
    provider_name: str | None = None
    model: str | None = None
    daily_budget_usd: float | None = None
    spent_today_usd: float = 0.0
    tasks_today: int = 0
    note: str
    # Set only when an enabled provider exists but its stored key cannot be used:
    # "undecryptable" (the SECRET_KEY changed after the key was saved) or "empty".
    # It tells the two situations apart, because "not configured" is false here.
    key_error: str | None = None


class AIModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(min_length=1, max_length=128)
    capability_tier: str = Field(default="standard", pattern="^(cheap|standard|high)$")
    input_cost_per_mtok: float = Field(default=0.0, ge=0)
    output_cost_per_mtok: float = Field(default=0.0, ge=0)


class AIModelUpdate(BaseModel):
    """Enable or disable one registered model (ADR-173).

    A model is only one of the two switches: it stays unroutable while its
    provider is disabled, and disabling it never deletes the row, so the AI task
    and usage history attached to it stays readable.
    """

    model_config = ConfigDict(extra="forbid")

    is_active: bool


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
    #: Raw model entries typed by the user; parsed with MQL's own field format so
    #: an id like ``google/gemma-4-31b-it:free`` survives verbatim (ADR-176).
    manual_models: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("manual_models")
    @classmethod
    def _bounded_manual_entries(cls, value: list[str]) -> list[str]:
        for text in value:
            if len(str(text).strip()) > 128:
                raise ValueError("each manual model entry must be 128 characters or fewer")
        return value

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
    #: Real number of models the provider reported; ``models_found`` is the full
    #: list, never truncated by MQL (ADR-176).
    models_total: int = 0


class AIProviderModelsUpdate(BaseModel):
    """An explicit model selection for one provider (ADR-176).

    ``models`` carry verbatim model ids (from the discovery list or the existing
    catalogue); ``manual_models`` carry raw text typed by the user, which the
    backend parses with MQL's own field format. Nothing is deleted here: a name
    missing from the selection is only switched off.
    """

    model_config = ConfigDict(extra="forbid")

    models: list[AIModelIn] = Field(default_factory=list)
    manual_models: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("manual_models")
    @classmethod
    def _bounded_manual_entries(cls, value: list[str]) -> list[str]:
        for text in value:
            if len(str(text).strip()) > 128:
                raise ValueError("each manual model entry must be 128 characters or fewer")
        return value


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


class SourceIngestIn(BaseModel):
    """Fetch and read one web page. Nothing here calls a model or spends AI budget.

    ``retention`` works exactly as it does for research material: the excerpt is
    capped at 500 characters unless the caller states the user owns the material
    (``retention="full"`` plus a ``license_note``), and even then the stored copy
    stays bounded — ``truncated`` tells the truth when it is.
    """

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(min_length=1, max_length=2048)
    source_ref: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    retention: Literal["excerpt", "full"] | None = None
    license_note: str | None = Field(default=None, max_length=2000)


class SourcePdfIn(BaseModel):
    """Read one PDF, either by URL or handed over inline as base64.

    This build has no multipart upload and no object storage, so an inline payload
    is the only way to read a PDF the platform cannot fetch — and it faces the same
    byte cap as a fetched one. Exactly one of ``uri`` / ``content_base64`` is
    required; supplying both or neither is a 400.
    """

    model_config = ConfigDict(extra="forbid")

    uri: str | None = Field(default=None, max_length=2048)
    content_base64: str | None = Field(default=None, max_length=3_000_000)
    filename: str | None = Field(default=None, max_length=255)
    source_ref: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    retention: Literal["excerpt", "full"] | None = None
    license_note: str | None = Field(default=None, max_length=2000)


class SourceSnapshotOut(BaseModel):
    """One stored observation: hashes, metadata and a bounded excerpt.

    The full text of a third-party document is deliberately absent, in the response
    as in the database (ADR-161): what came in is described by its hashes and read
    limits, and only the retained excerpt is echoed back.
    """

    snapshot_id: int
    source_kind: str
    snapshot_status: str
    parse_status: str
    source_ref: str | None = None
    label: str | None = None
    original_uri: str | None = None
    final_uri: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    bytes_read: int = 0
    chars_read: int = 0
    source_hash: str | None = None
    text_hash: str | None = None
    parser: str | None = None
    parser_version: str | None = None
    robots_ok: bool | None = None
    retention: dict[str, Any] = Field(default_factory=dict)
    excerpt: list[str] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    redirects: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    fetched_at: str | None = None
    created_at: str | None = None


class ResearchSourceIn(BaseModel):
    """One piece of research material.

    ``text`` is how the caller hands material over. Since v2.1.0 a ``url`` or
    ``pdf`` source may instead be named by ``uri`` (the platform fetches and parses
    it) or by ``snapshot_id`` (the platform already observed it through
    ``POST /ai/sources/url`` or ``POST /ai/sources/pdf``). If both ``text`` and a
    ``uri``/``snapshot_id`` are given, the text wins and the run records a warning:
    the caller's own copy of the material is never second-guessed by a fetch.

    ``retention`` decides how much of the material is kept as excerpt: the
    user's own words are kept, material from elsewhere keeps 500 characters
    unless the caller states the user owns it (``retention="full"`` plus a
    ``license_note``).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["user_input", "text", "github_file", "url", "pdf"] = "user_input"
    text: str | None = Field(default=None, min_length=1, max_length=40_000)
    source_ref: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    uri: str | None = Field(default=None, max_length=2048)
    snapshot_id: int | None = Field(default=None, ge=1)
    license_note: str | None = Field(default=None, max_length=2000)
    retention: Literal["excerpt", "full"] | None = None


class ResearchRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=4000)
    sources: list[ResearchSourceIn] = Field(min_length=1, max_length=8)
    model: str | None = Field(default=None, max_length=128)


class FormalizeIn(BaseModel):
    """Re-run only the architect, for a hypothesis that is already stored."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: int | None = Field(default=None, ge=1)
    run_id: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, max_length=128)


class CompileDraftIn(BaseModel):
    """The only input a compile request may carry (docs/29 §16.7).

    A draft is compiled by *naming its target*, never by handing the server a
    spec: no ``dsl``, no ``compile_hash``, no ``compiler_version``. ``extra="forbid"``
    is what makes that a refusal instead of a silently ignored field.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_id: int = Field(ge=1)


class DraftConfirmationIn(BaseModel):
    """A human answer about one draft (v2.4.0 Step 1).

    ``needs_revision`` is a third answer on purpose: a reviewer who wants the
    draft reworked should not have to reject the run to say so, and the record
    should say which of the two happened.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["confirmed", "rejected", "needs_revision"]
    note: str | None = Field(default=None, max_length=2000)


class ResearchRunOut(BaseModel):
    """A research run and whatever it produced, including its refusals."""

    run_id: int
    question: str
    status: str
    current_step: str
    capability_status: str | None = None
    attempts: int = 0
    sources: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    researcher_task_id: int | None = None
    architect_task_id: int | None = None
    created_at: str | None = None
    completed_at: str | None = None
    hypothesis: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None


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
