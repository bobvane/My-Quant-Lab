/**
 * API client.
 *
 * The UI never computes quant numbers itself: every metric displayed here comes
 * from the deterministic engine via the REST API.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      detail = body?.detail ?? body?.error?.message ?? detail
    } catch {
      /* keep default detail */
    }
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export interface HealthResponse {
  status: string
  version: string
  database: string
  redis: string
  workers: string
  environment: string
  feature_version: string
  engine_version: string
}

export interface SystemInfo {
  app_name: string
  version: string
  environment: string
  market_data_provider: string
  default_currency: string
  default_timezone: string
  feature_version: string
  engine_version: string
  strategy_schema_version: string
  modules: string[]
}

export interface Asset {
  id: number
  symbol: string
  display_name: string | null
  asset_class: string
  currency: string
  exchange: string | null
  is_active: boolean
}

export interface Strategy {
  id: number
  name: string
  slug: string
  description: string | null
  source_type: string
  /** Provenance: where this strategy came from, and under which licence (ADR-114). */
  source_url: string | null
  license: string | null
  author: string | null
  status: string
  lifecycle: string
  created_at: string
  version_count: number
}

export interface StrategyVersion {
  id: number
  strategy_id: number
  version: string
  schema_version: string
  immutable_hash: string
  validation_status: string
  is_current: boolean
  created_at: string
  dsl: Record<string, unknown>
}

export interface BacktestSummary {
  id: number
  strategy_version_id: number
  dataset_version_id: number
  engine_version: string
  feature_version: string
  status: string
  dataset_hash: string
  created_at: string
  total_return: number | null
  max_drawdown: number | null
  sharpe: number | null
  win_rate: number | null
  number_of_trades: number | null
  final_equity: number | null
  /** Which series the run used — needed to tell whether two runs are comparable. */
  symbol: string | null
  timeframe: string | null
}

export interface EquityPoint {
  timestamp: string
  equity: number
  cash: number
  position_value: number
  close: number
}

export interface BacktestDetail extends BacktestSummary {
  metrics: Record<string, number | null>
  equity_curve: EquityPoint[]
  trades: Array<Record<string, unknown>>
  result_hash: string
  parameters: Record<string, unknown>
  execution_model: Record<string, unknown>
  warnings: string[]
}

/** What the importer actually read, and what it never looked at (docs/05 §4.1). */
export interface GithubCoverage {
  analysis_version: string
  candidate_files: number
  candidate_python_files: number
  cap: number
  attempted_files: number
  downloaded_files: number
  parsed_files: number
  inventoried_files: number
  skipped_files: number
  /** Downloaded but not understood: nothing in these files was read as rules. */
  unparsed_python_files: number
  not_attempted_files: number
  unread_python_files: number
  complete: boolean
  /** Wall-clock budget the fetch was given; null when it was unbounded. */
  max_seconds: number | null
  /** The budget is what stopped this read, not the cap: the advice differs. */
  budget_exhausted: boolean
}

export interface GithubSkippedFile {
  path: string
  reason: string
}

export interface GithubAnalysis {
  owner: string
  repo: string
  ref: string
  /** The revision `ref` pointed at when the fetch started (ADR-060). */
  commit: string
  description: string | null
  license: string | null
  analysis_version: string
  coverage: GithubCoverage
  files_parsed: string[]
  files_inventoried: string[]
  files_skipped: GithubSkippedFile[]
  /** Files whose parse failed: read, but nothing was understood (ADR-059). */
  files_unparsed: GithubSkippedFile[]
  indicators: Array<Record<string, unknown>>
  rules: Array<Record<string, unknown>>
  params: Array<Record<string, unknown>>
  unknowns: Array<Record<string, unknown>>
  unsafe_flags: Array<Record<string, unknown>>
  draft_dsl: Record<string, unknown>
  warnings: string[]
}

export interface GithubImportResult {
  strategy_id: number
  strategy_version_id: number
  version: string
  /** True when the server picked the version because the request named none (ADR-061). */
  version_assigned: boolean
  /** The commit recorded on the new version: the one the analysis read (ADR-060). */
  source_commit: string
  validation_status: string
  immutable_hash: string
  warnings: Array<Record<string, unknown>>
}

/**
 * What an import of `name` would do: the versions that slug already has and the
 * one the server would assign. `can_assign` is false when the existing versions
 * cannot be read as `major.minor.patch`, so a version has to be named (ADR-061).
 */
export interface GithubVersionPlan {
  name: string
  slug: string
  strategy_id: number | null
  versions: string[]
  next_version: string | null
  can_assign: boolean
  reason: string
}

/**
 * One recorded check of a watched source. `extraction` is where the *reason*
 * lives (`imported`, `reason`, `transient`, `coverage`, `warnings`); rows written
 * before ADR-058 can carry an empty object.
 */
export interface GithubSnapshot {
  id: number
  source_id: number
  commit: string
  content_hash: string
  fetched_at: string
  extraction: Record<string, any>
}

/** `POST /strategies/validate` — a statement about one exact DSL document (ADR-113). */
export interface StrategyValidation {
  is_valid: boolean
  issues: Array<Record<string, unknown>>
  available_columns: string[]
}

/** Percentile block of a Monte Carlo distribution (docs/22). */
export interface MonteCarloPercentiles {
  p5: number | null
  p25: number | null
  p50: number | null
  p75: number | null
  p95: number | null
}

export interface MonteCarloResult {
  monte_carlo_version: string
  seed: number
  timeframe: string
  /** Named so consumers cannot mistake resampling for a forecast. */
  method: string
  summary: {
    runs: number
    observed_trades: number
    trades_per_run: number
    initial_capital: number
    final_equity: MonteCarloPercentiles
    total_return: MonteCarloPercentiles
    max_drawdown: MonteCarloPercentiles
    sharpe: MonteCarloPercentiles
    sortino: MonteCarloPercentiles
    probability_of_profit: number
    probability_of_loss: number
    probability_of_ruin: number
    expected_total_return: number
    expected_max_drawdown: number
    worst_max_drawdown: number
  }
  sample_equity_paths: number[][]
  warnings: string[]
}

/** One member of an ensemble vote (docs/24). */
export interface EnsembleMemberIn {
  strategy_version_id: number
  weight: number
}

export interface EnsembleMemberOut {
  label: string
  weight: number
  entry_bars: number
  exit_bars: number
  /** How many of this member's own entry signals survived the vote. */
  entry_agreed: number
  /** Entry signals this member raised where the vote was split and nothing happened. */
  solo_entries: number
  /** `entry_agreed / entry_bars`, or null when the member never signalled. */
  entry_support_rate: number | null
  /** Share of all evaluated bars where this member's vote matched the outcome. */
  vote_agreement_rate: number | null
}

/**
 * One member run by the ensemble itself, on the ensemble's own bars with its own cost
 * model — as opposed to that member's stored backtest, which may have used another
 * window or fee model and would make the comparison table incomparable.
 *
 * Each run is funded with `initial_capital` of its own: an independent account, not a
 * concurrent second position (the engine holds one position at a time).
 */
export interface EnsembleMemberRun {
  label: string
  weight: number
  initial_capital: number
  final_equity: number
  /** Positions this member would have opened alone; matches `metrics.number_of_trades`. */
  entries_taken: number
  metrics: Record<string, number | null>
  equity_curve: EquityPoint[]
}

export interface EnsembleResult {
  ensemble_version: string
  vote_threshold: number
  members: EnsembleMemberOut[]
  /** One entry per member, in the same order as `members`. */
  member_runs: EnsembleMemberRun[]
  bars_evaluated: number
  agreement: {
    entry_bars: number
    exit_bars: number
    short_entry_bars: number
    /**
     * Bars where the vote fired AND the portfolio was flat, i.e. positions actually
     * opened. This — not `entry_bars` — is comparable to a member's entry count.
     */
    entries_taken: number
    /** Bars where at least one member wanted to enter, i.e. the vote's denominator. */
    signalled_bars: number
    /** Of those, bars where only one member wanted in — split votes that did nothing. */
    solo_signalled_bars: number
    /** `entry_bars / signalled_bars`: how much of the members' willingness survived. */
    entry_support_rate: number | null
    exit_support_rate: number | null
  }
  metrics: Record<string, number | null>
  trades: Array<Record<string, unknown>>
  equity_curve: Array<Record<string, unknown>>
  final_equity: number
  initial_capital: number
  warnings: string[]
  /** Dataset the vote ran on; compare against a member's run to check comparability. */
  dataset_version_id: number | null
  symbol: string | null
  timeframe: string
  engine_version: string
  feature_version: string
}

/** One evaluated threshold of an ensemble vote sweep (docs/24 §7, ADR-052). */
export interface EnsembleSweepPoint {
  vote_threshold: number
  /**
   * The smallest attainable vote that clears this threshold — the coalition the
   * threshold is really waiting for. Between two attainable votes nothing changes,
   * which is why this surface is a staircase and not a curve.
   */
  effective_vote: number
  entries_taken: number
  entry_bars: number
  signalled_bars: number
  solo_signalled_bars: number
  final_equity: number
  total_return: number
  max_drawdown: number
  sharpe: number | null
  win_rate: number | null
  number_of_trades: number
}

export interface EnsembleSweepResult {
  ensemble_version: string
  engine_version: string
  feature_version: string
  bars_evaluated: number
  initial_capital: number
  thresholds: number[]
  points: EnsembleSweepPoint[]
  members: Array<{ label: string; weight: number; weight_share: number }>
  /** Every distinct total the weighted vote can take. */
  possible_votes: number[]
  /** Longest threshold list the sweep will evaluate; ask for more and it is a 422. */
  max_thresholds: number
  warnings: string[]
  dataset_version_id: number | null
  symbol: string | null
  timeframe: string
}

/** One evaluated grid point of a sensitivity sweep (docs/21). */
export interface SensitivityPoint {
  parameters: Record<string, number | string>
  objective: number | null
  metrics: Record<string, number | null>
  result_hash: string
  warnings: string[]
  /**
   * The whole window sat inside the strategy's warm-up, so this point never got an
   * evaluable bar: its metrics are the flat zeros of a strategy that did not run, not
   * a measurement. Excluded from the ranking, the summary and the stability verdict.
   */
  warmup_unmet: boolean
}

export interface SensitivityResult {
  sensitivity_version: string
  metric: string
  axes: Record<string, Array<number | string>>
  grid_points: number
  evaluated_points: number
  /** Points that were actually measured, i.e. what the ranking was computed from. */
  ranked_points: number
  warmup_unmet_points: number
  warnings: string[]
  points: SensitivityPoint[]
  summary: {
    mean: number | null
    median: number | null
    stdev: number | null
    min: number | null
    max: number | null
    range: number | null
    positive_ratio: number | null
  }
  best: { parameters: Record<string, number | string>; objective: number; result_hash: string } | null
  worst: { parameters: Record<string, number | string>; objective: number; result_hash: string } | null
  /** All evaluated points share a sign. true only means sign-consistent, not good. */
  stable: boolean | null
}

export interface AIStatus {
  configured: boolean
  provider_name: string | null
  model: string | null
  daily_budget_usd: number | null
  spent_today_usd: number
  tasks_today: number
  note: string
}

export interface AIExplanation {
  summary: string
  why?: string[]
  key_drivers?: string[]
  risks?: string[]
  risk_notes?: string[]
  what_could_invalidate?: string[]
  what_to_watch_next: string[]
  plain_language: string
}

export interface ExplainResult {
  explanation: AIExplanation
  cached: boolean
  task_id: number | null
  model: string | null
  cost_usd_estimated: number
}

export interface AIProviderRecord {
  id: number
  name: string
  provider_type: string
  base_url: string
  default_model: string | null
  is_active: boolean
  daily_budget_usd: number
  api_key_set: boolean
  key_masked: string
  models: Array<Record<string, unknown>>
}

export interface ProviderTestResult {
  ok: boolean
  detail: string
  models_found: string[]
}

export interface NotificationChannelRecord {
  id: string
  type: string
  enabled: boolean
  [key: string]: unknown
}

export interface NotificationConfig {
  enabled: boolean
  configured: boolean
  include_wait: boolean
  quiet_hours: string
  daily_max: number
  cooldown_minutes: number
  base_url: string
  eligible_states: string[]
  channels: NotificationChannelRecord[]
}

export interface NotificationTestResult {
  ok: boolean
  detail: string
  results: Array<Record<string, unknown>>
}

export interface LifecycleStage {
  stage: string
  group: string
  reached: boolean
}

export interface StrategyLifecycle {
  strategy_id: number
  name: string
  current: string
  current_group: string
  suggested_next: string | null
  blocked_reason: string | null
  reference_eligible: boolean
  degraded: boolean
  degrade_reason: string | null
  stages: LifecycleStage[]
  gates: Record<string, boolean>
  evidence: Record<string, any>
  thresholds: Record<string, any>
  manual_only_stages: string[]
}

export interface LifecycleApplyResult {
  strategy_id: number
  previous: string
  current: string
  applied: boolean
  detail: string
}

export interface SignalRecord {
  id: number
  strategy_version_id: number
  asset_id: number
  symbol: string | null
  strategy_name: string | null
  strategy_version: string | null
  timeframe: string
  bar_timestamp: string
  state: string
  direction: string
  /**
   * Set on a closing signal: which side it closes. `direction` is `FLAT` there,
   * so without this the panel cannot tell a closed long from a closed short
   * (ADR-115).
   */
  closes_direction: string | null
  price_reference: number | null
  stop_reference: number | null
  target_reference: number | null
  triggered_rules: string[]
  portfolio_context: Record<string, any> | null
  status: string
  generated_at: string
  notified_at: string | null
  explanation: Record<string, any> | null
}

export interface SignalIntent {
  state: string
  direction: string
  closes_direction?: string | null
  reason?: string
  bar_time?: string | null
  price_reference?: number | null
  stop_reference?: number | null
  target_reference?: number | null
  triggered_rules?: string[]
  feature_snapshot_hash?: string
  symbol?: string
  timeframe?: string
  strategy_version_id?: number
}

export interface AppSettingsEnvironment {
  app_version: string
  environment: string
  market_data_provider: string
  default_currency: string
  default_timezone: string
  ai_daily_budget_usd: number
  /**
   * True when the backend has a Ghostfolio base URL. The dashboard must not ask
   * for holdings when this is false: an optional integration that was never
   * configured must not look broken on every page load (ADR-067).
   */
  ghostfolio_configured: boolean
}

export interface AppSettings {
  settings: Array<Record<string, unknown>>
  environment: AppSettingsEnvironment
}

export interface TemporaryAccessState {
  /** disabled | starting | active | stopping | error (ADR-125). */
  status: string
  /** The public address, once cloudflared has reported one. */
  url: string | null
  started_at: string | null
  expires_at: string | null
  /** Seconds left before the automatic shutdown; null when no tunnel is running. */
  remaining_seconds: number | null
  max_duration_seconds: number
  /** False when the deployment switched the feature off. */
  enabled: boolean
  /** The only service the tunnel may reach — the bundled web container. */
  target_url: string
  /** Why the last attempt failed, or why a tunnel closed itself. */
  detail: string | null
}

export interface PaperAccount {
  id: number
  name: string
  /** The strategy this account is bound to, if any: attribution is per account (ADR-114). */
  strategy_id: number | null
  /** Money the account was funded with: deposits minus withdrawals (ADR-066). */
  net_deposits: number
  cash: number
  /**
   * Realized P&L of the closed trades. P&L is not `cash - net_deposits`: an open
   * position has spent the cash, so a full-size buy would read as -100% (ADR-124).
   */
  realized_pnl: number
  base_currency: string
  status: string
  reset_count: number
  created_at: string
}

export interface PaperPosition {
  id: number
  account_id: number
  asset_id: number
  quantity: number
  avg_cost: number
  realized_pnl: number
}

export interface PaperExecution {
  account_id: number
  side: string
  order_id: number
  quantity: number
  fill_price: number
  fees: number
  slippage: number
  realized_pnl: number
  cash: number
}

export const api = {
  health: () => request<HealthResponse>('/health'),
  systemInfo: () => request<SystemInfo>('/system/info'),
  assets: () => request<Asset[]>('/assets'),
  strategies: () => request<Strategy[]>('/strategies'),
  // One strategy, for the nine-part detail page: the list row carries no provenance (ADR-114).
  strategy: (id: number) => request<Strategy>(`/strategies/${id}`),
  strategyVersions: (id: number) => request<StrategyVersion[]>(`/strategies/${id}/versions`),
  activateVersion: (versionId: number) =>
    request<StrategyVersion>(`/strategy-versions/${versionId}/activate`, { method: 'PUT' }),
  strategyLineage: (strategyId: number) =>
    request<Record<string, any>>(`/strategies/${strategyId}/lineage`),
  // Recomputes the stored immutable hash: a version whose text no longer hashes to
  // what was archived is a different strategy wearing the same name (ADR-114).
  verifyStrategyVersion: (versionId: number) =>
    request<{
      strategy_version_id: number
      version: string
      stored_hash: string
      recomputed_hash: string
      intact: boolean
    }>(`/strategies/versions/${versionId}/verify`),
  versionParameters: (versionId: number) =>
    request<Array<Record<string, any>>>(`/strategy-versions/${versionId}/parameters`),
  createStrategy: (name: string, description?: string) =>
    request<Strategy>('/strategies', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    }),
  createVersion: (strategyId: number, version: string, dsl: Record<string, unknown>) =>
    request<StrategyVersion>(`/strategies/${strategyId}/versions`, {
      method: 'POST',
      body: JSON.stringify({ version, dsl }),
    }),
  validateDsl: (dsl: Record<string, unknown>) =>
    request<StrategyValidation>('/strategies/validate', {
      method: 'POST',
      body: JSON.stringify(dsl),
    }),
  syncMarketData: (symbol: string, timeframe = '1d', lookbackDays = 400) =>
    request<Record<string, unknown>>('/market-data/sync', {
      method: 'POST',
      body: JSON.stringify({ symbol, timeframe, lookback_days: lookbackDays }),
    }),
  // Deleting a series a backtest used archives it instead (ADR-081): the run's
  // dataset pointer is the reproducibility evidence, so the data stays. `purge`
  // says "really delete", and the API answers 409 while runs depend on it.
  deleteSeries: (seriesId: number, purge = false) =>
    request<{
      deleted: number | null
      symbol: string
      archived: boolean
      blocking_runs: number
      message: string
    }>(`/market-data/series/${seriesId}${purge ? '?purge=true' : ''}`, {
      method: 'DELETE',
    }),
  restoreSeries: (seriesId: number) =>
    request<{ id: number; is_archived: boolean; blocking_runs: number }>(
      `/market-data/series/${seriesId}/restore`,
      { method: 'POST' },
    ),
  series: (includeArchived = false) =>
    request<Array<Record<string, unknown>>>(
      `/market-data/series${includeArchived ? '?include_archived=true' : ''}`,
    ),
  // One series with its raw readings (bar count, content hash, source): the data
  // page shows these in advanced mode only (评审 §7、§19；ADR-131).
  seriesDetail: (seriesId: number) =>
    request<Record<string, any>>(`/market-data/series/${seriesId}`),
  latestBars: (symbol: string, timeframe = '1d', limit = 120) =>
    request<{ bars: Array<{ timestamp: string; close: number; high: number; low: number; open: number }> }>(
      `/market-data/latest/${encodeURIComponent(symbol)}?timeframe=${timeframe}&limit=${limit}`,
    ),
  backtests: (strategyVersionId?: number) =>
    request<BacktestSummary[]>(
      `/backtests${strategyVersionId ? `?strategy_version_id=${strategyVersionId}` : ''}`,
    ),
  backtest: (id: number) => request<BacktestDetail>(`/backtests/${id}`),
  // Every strategy version across all strategies. The ensemble needs to vote with
  // versions of *different* strategies, so scoping candidates to one strategy (as
  // `/strategies/{id}/versions` does) would make cross-strategy voting unreachable.
  allStrategyVersions: () => request<StrategyVersion[]>('/strategy-versions'),
  compareBacktests: (ids: number[]) =>
    request<{ metrics: string[]; runs: Array<Record<string, any>> }>(
      `/backtests/compare?ids=${ids.join(',')}`,
    ),
  runBacktest: (
    strategyVersionId: number,
    symbol: string,
    timeframe = '1d',
    /**
     * Execution-level overrides (docs/23). Keys are execution fields, e.g.
     * `{ sizing: { mode: 'risk_per_trade', risk_pct: 0.01 } }`.
     */
    executionOverrides: Record<string, unknown> = {},
    /** Inclusive bar window as ISO timestamps; omitted means "everything loaded". */
    start?: string,
    end?: string,
  ) =>
    request<BacktestDetail>('/backtests', {
      method: 'POST',
      body: JSON.stringify({
        strategy_version_id: strategyVersionId,
        symbol,
        timeframe,
        execution_overrides: executionOverrides,
        ...(start ? { start } : {}),
        ...(end ? { end } : {}),
      }),
    }),
  runOos: (strategyVersionId: number, symbol: string, oosPct = 0.2, timeframe = '1d') =>
    request<{
      split_time: string
      in_sample_bars: number
      out_of_sample_bars: number
      in_sample: Record<string, number | null>
      out_of_sample: Record<string, number | null>
    }>('/research/oos', {
      method: 'POST',
      body: JSON.stringify({
        strategy_version_id: strategyVersionId,
        symbol,
        timeframe,
        oos_pct: oosPct,
      }),
    }),
  walkForward: (strategyVersionId: number, symbol: string, trainBars = 200, testBars = 60) =>
    request<Record<string, unknown>>('/research/walk-forward', {
      method: 'POST',
      body: JSON.stringify({
        strategy_version_id: strategyVersionId,
        symbol,
        train_bars: trainBars,
        test_bars: testBars,
      }),
    }),
  // Parameter sensitivity sweep (docs/21). Descriptive only: it reports how the
  // metrics respond across a parameter grid; it never recommends parameters.
  sensitivity: (
    strategyVersionId: number,
    symbol: string,
    grid: Record<string, Array<number | string>>,
    metric = 'sharpe',
    timeframe = '1d',
  ) =>
    request<SensitivityResult>('/research/sensitivity', {
      method: 'POST',
      body: JSON.stringify({
        strategy_version_id: strategyVersionId,
        symbol,
        timeframe,
        grid,
        metric,
      }),
    }),
  // Monte Carlo resampling of a stored backtest's trades (docs/22). Resamples
  // history; it is not a forecast, and it never re-runs the backtest.
  monteCarlo: (backtestRunId: number, runs = 1000, seed = 0, tradesPerRun?: number) =>
    request<MonteCarloResult>('/research/monte-carlo', {
      method: 'POST',
      body: JSON.stringify({
        backtest_run_id: backtestRunId,
        runs,
        seed,
        trades_per_run: tradesPerRun,
      }),
    }),
  // Weighted vote across several strategy versions, executed as ONE portfolio
  // (docs/24). A member must be agreed with, not merely present.
  ensemble: (
    members: EnsembleMemberIn[],
    symbol: string,
    voteThreshold = 0.5,
    executionOverrides: Record<string, unknown> = {},
  ) =>
    request<EnsembleResult>('/research/ensemble', {
      method: 'POST',
      body: JSON.stringify({
        members,
        symbol,
        vote_threshold: voteThreshold,
        execution_overrides: executionOverrides,
      }),
    }),
  // The same vote at several thresholds (docs/24 §7). Descriptive: it shows the
  // staircase so a skipped coalition is visible, and never picks a threshold.
  ensembleSweep: (
    members: EnsembleMemberIn[],
    symbol: string,
    thresholds: number[] | null = null,
    executionOverrides: Record<string, unknown> = {},
  ) =>
    request<EnsembleSweepResult>('/research/ensemble/sweep', {
      method: 'POST',
      body: JSON.stringify({
        members,
        symbol,
        thresholds,
        execution_overrides: executionOverrides,
      }),
    }),
  signals: (state?: string, limit = 100, symbol?: string, offset = 0) =>
    request<SignalRecord[]>(
      `/signals?limit=${limit}&offset=${offset}${state ? `&state=${encodeURIComponent(state)}` : ''}${
        symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''
      }`,
    ),
  acknowledgeSignal: (id: number) =>
    request<Record<string, unknown>>(`/signals/${id}/acknowledge`, { method: 'POST' }),
  signalEvidence: (id: number) => request<Record<string, any>>(`/signals/${id}/evidence`),
  // The five deterministic layers for one strategy version (rule match → empirical
  // stats → paper stats → portfolio context → intent). The AI explanation is only
  // ever *added* on top of these, never substituted for them (ADR-112).
  strategyEvidence: (strategyVersionId: number, symbol?: string, timeframe = '1d') =>
    request<Record<string, any>>(
      `/signals/evidence/${strategyVersionId}?timeframe=${encodeURIComponent(timeframe)}${
        symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''
      }`,
    ),
  // The current (unpersisted) signal for one strategy version: a preview is what a
  // signal *would* be right now, and it needs a symbol because a strategy does not
  // name one (ADR-114).
  signalPreview: (strategyVersionId: number, symbol?: string, timeframe = '1d') =>
    request<Record<string, any>>(
      `/signals/preview/${strategyVersionId}?timeframe=${encodeURIComponent(timeframe)}${
        symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''
      }`,
    ),
  signalOutcomes: (limit = 50, symbol?: string) =>
    request<Array<Record<string, any>>>(
      `/signals/outcomes?limit=${limit}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''}`,
    ),
  signalOutcomeSummary: (symbol?: string) =>
    request<{
      symbol: string | null
      signals: number
      decided: number
      undecided: number
      bars_after: number
      groups: Record<string, Record<string, any>>
    }>(`/signals/outcome-summary${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`),
  scanSignals: (persist = false) =>
    request<{ evaluated: number; created: number; signals: SignalIntent[]; disclaimer: string }>(
      `/signals/scan?persist=${persist}`,
      { method: 'POST' },
    ),
  paperAccounts: () => request<PaperAccount[]>('/paper/accounts'),
  paperPerformance: (accountId: number) =>
    request<Record<string, any>>(`/paper/accounts/${accountId}/performance`),
  // The curve is a history: its last point is net deposits + realized P&L, and a deposit
  // is a step in it rather than a gain (ADR-108).
  paperEquity: (accountId: number) =>
    request<{
      account_id: number
      cash: number
      net_deposits: number
      realized_pnl: number
      trades_count: number
      equity_curve: Array<{ timestamp: string; equity: number }>
      curve_note: string
    }>(`/paper/accounts/${accountId}/equity`),
  paperPositions: (accountId: number) =>
    request<PaperPosition[]>(`/paper/accounts/${accountId}/positions`),
  // Trades carry the strategy *version label* they came from, not a strategy id:
  // attribution is per account, and this page says so rather than inventing a join
  // the backend does not make (ADR-114).
  paperTrades: (accountId?: number, limit = 100) =>
    request<Array<Record<string, any>>>(
      `/paper/trades?limit=${limit}${accountId ? `&account_id=${accountId}` : ''}`,
    ),
  executePaperSignal: (accountId: number, signalId: number) =>
    request<PaperExecution>(`/paper/accounts/${accountId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ signal_id: signalId }),
    }),
  closePaperAccount: (accountId: number) =>
    request<{ account_id: number; status: string }>(`/paper/accounts/${accountId}/close`, {
      method: 'POST',
    }),
  reopenPaperAccount: (accountId: number) =>
    request<{ account_id: number; status: string }>(`/paper/accounts/${accountId}/reopen`, {
      method: 'POST',
    }),
  resetPaperAccount: (accountId: number, initialCash?: number) =>
    request<{
      account_id: number
      cash: number
      net_deposits: number
      reset_count: number
      warning: string
    }>(
      `/paper/accounts/${accountId}/reset${
        initialCash != null ? `?initial_cash=${initialCash}` : ''
      }`,
      { method: 'POST' },
    ),
  fundPaperAccount: (accountId: number, amount: number) =>
    request<{ account_id: number; cash: number }>(`/paper/accounts/${accountId}/fund`, {
      method: 'POST',
      body: JSON.stringify({ amount }),
    }),
  createPaperAccount: (name: string, initialCash: number) =>
    request<PaperAccount>('/paper/accounts', {
      method: 'POST',
      body: JSON.stringify({ name, initial_cash: initialCash }),
    }),
  settings: () => request<AppSettings>('/settings'),
  updateSetting: (key: string, value: string) =>
    request<Record<string, unknown>>('/settings', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),
  /**
   * Temporary remote access: a Cloudflare Quick Tunnel the operator opens by hand
   * and closes by hand (or by its deadline). The state lives in the API process,
   * so a restart answers `disabled` and nothing is exposed until it is asked for
   * again (ADR-125).
   */
  temporaryAccess: () => request<TemporaryAccessState>('/settings/temporary-access'),
  startTemporaryAccess: () =>
    request<TemporaryAccessState>('/settings/temporary-access/start', { method: 'POST' }),
  stopTemporaryAccess: () =>
    request<TemporaryAccessState>('/settings/temporary-access/stop', { method: 'POST' }),
  audit: () => request<{ total: number; events: Array<Record<string, unknown>> }>('/audit/logs'),
  analyzeGithubRepo: (
    repoUrl: string,
    ref?: string,
    token?: string,
    maxFiles = 12,
    maxSeconds = 120,
  ) =>
    request<GithubAnalysis>('/importer/github/analyze', {
      method: 'POST',
      body: JSON.stringify({
        repo_url: repoUrl,
        ref: ref || undefined,
        token: token || undefined,
        max_files: maxFiles,
        max_seconds: maxSeconds,
      }),
    }),
  githubSources: () => request<Array<Record<string, any>>>('/importer/github/sources'),
  githubSnapshots: (id: number, limit = 20) =>
    request<GithubSnapshot[]>(`/importer/github/sources/${id}/snapshots?limit=${limit}`),
  githubCheckSource: (id: number) =>
    request<{ has_update: boolean; head: string | null; current_commit: string | null }>(
      `/importer/github/sources/${id}/check`,
    ),
  /**
   * What an import would do before it does it: the versions this name already has
   * and the one the server would assign next (ADR-061).
   */
  githubVersionPlan: (name: string) =>
    request<GithubVersionPlan>(`/importer/github/versions?name=${encodeURIComponent(name)}`),
  /**
   * Import a reviewed draft. `commit` is required: the version has to name the
   * revision the analysis read, and a branch name is not a revision (ADR-060).
   * Omitting `version` lets the server assign the next free one (ADR-061).
   */
  importGithubStrategy: (
    repoUrl: string,
    name: string,
    dsl: Record<string, unknown>,
    commit: string,
    ref?: string,
    version?: string,
  ) =>
    request<GithubImportResult>('/importer/github/import', {
      method: 'POST',
      body: JSON.stringify({
        repo_url: repoUrl,
        name,
        version: version || undefined,
        dsl,
        commit,
        ref: ref || undefined,
      }),
    }),
  aiStatus: () => request<AIStatus>('/ai/status'),
  explainSignalPreview: (strategyVersionId: number, symbol?: string, timeframe = '1d') =>
    request<ExplainResult>('/signals/preview-explain', {
      method: 'POST',
      body: JSON.stringify({ strategy_version_id: strategyVersionId, symbol, timeframe }),
    }),
  explainSignal: (signalId: number) =>
    request<ExplainResult>(`/signals/${signalId}/explain`, { method: 'POST' }),
  explainBacktest: (runId: number) =>
    request<ExplainResult>(`/backtests/${runId}/explain`, { method: 'POST' }),
  aiModels: () => request<{ models: Array<Record<string, any>> }>('/ai/models'),
  aiPrompts: () => request<{ prompts: Array<Record<string, any>> }>('/ai/prompts'),
  aiTasksList: (limit = 50) => request<Array<Record<string, any>>>(`/ai/tasks?limit=${limit}`),
  // Read-only audit detail of one AI task: structured output, token usage and
  // error. Secrets are never echoed back by the API.
  aiTask: (taskId: number) => request<Record<string, any>>(`/ai/tasks/${taskId}`),
  aiUsage: (limit = 100) => request<{ usage: Array<Record<string, any>> }>(`/ai/usage?limit=${limit}`),
  auditForEntity: (entityType: string, entityId: string) =>
    request<{ total: number; events: Array<Record<string, unknown>> }>(
      `/audit/logs/entity/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}`,
    ),
  aiProviders: () =>
    request<{ providers: AIProviderRecord[]; note: string }>('/settings/ai/providers'),
  createAiProvider: (payload: Record<string, unknown>) =>
    request<AIProviderRecord>('/settings/ai/providers', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteStrategy: (id: number) =>
    request<{ deleted: number; name: string }>(`/strategies/${id}`, { method: 'DELETE' }),
  deleteBacktest: (id: number) =>
    request<{ deleted: number }>(`/backtests/${id}`, { method: 'DELETE' }),
  updateAiProvider: (id: number, payload: Record<string, unknown>) =>
    request<AIProviderRecord>(`/settings/ai/providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteAiProvider: (id: number) =>
    request<{ deleted: number; name: string }>(`/settings/ai/providers/${id}`, {
      method: 'DELETE',
    }),
  testAiProvider: (id: number) =>
    request<ProviderTestResult>(`/settings/ai/providers/${id}/test`, { method: 'POST' }),
  testNewAiProvider: (baseUrl: string, apiKey: string) =>
    request<ProviderTestResult>('/settings/ai/providers/test', {
      method: 'POST',
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
    }),
  lifecycles: () => request<StrategyLifecycle[]>('/lifecycle/strategies'),
  lifecycle: (strategyId: number) =>
    request<StrategyLifecycle>(`/lifecycle/strategies/${strategyId}`),
  applyLifecycle: (strategyId: number, targetStage: string, note?: string) =>
    request<LifecycleApplyResult>(`/lifecycle/strategies/${strategyId}/apply`, {
      method: 'POST',
      body: JSON.stringify({ target_stage: targetStage, note }),
    }),
  notificationConfig: () => request<NotificationConfig>('/notifications/config'),
  updateNotificationConfig: (payload: Record<string, unknown>) =>
    request<NotificationConfig>('/notifications/config', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  testNotification: () =>
    request<NotificationTestResult>('/notifications/test', { method: 'POST' }),
  notificationEvents: (limit = 50) =>
    request<{ events: Array<Record<string, unknown>> }>(`/notifications/events?limit=${limit}`),
  getGhostfolioHoldings: () =>
    request<{
      holdings: Array<Record<string, any>>
      holdings_count?: number
      total_value?: number | null
      total_cost?: number | null
      total_pnl?: number | null
      total_pnl_pct?: number | null
      source?: string
    }>('/settings/ghostfolio/holdings'),
  testGhostfolio: () => request<Record<string, any>>('/settings/ghostfolio/test'),
  resourcesCurrent: () => request<Record<string, any>>('/resources/current'),
  resourcesHistory: (metric: 'cpu' | 'ram', range: '1h' | '24h' | '7d' | '30d') =>
    request<Record<string, any>>(`/resources/history?metric=${metric}&range=${range}`),
  resourcesEvents: (limit = 30) =>
    request<{ events: Array<Record<string, unknown>> }>(`/resources/events?limit=${limit}`),
  resourcesSummary: () => request<Record<string, any>>('/resources/summary'),
}
