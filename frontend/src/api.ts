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
    /**
     * The machine-readable code the server used (`error.code`), or `null`.
     *
     * Callers must branch on this and not on `message`: the API answers in
     * English, and a refusal that has a code has a name the UI can translate.
     */
    readonly code: string | null = null,
    /** The structured fields the server attached to its refusal (`error.details`). */
    readonly details: Record<string, any> = {},
    /**
     * The raw error body. A 422 from the strategy compiler is `{result, report}`
     * with no `error` envelope at all, so this is the only place to read it.
     */
    readonly body: Record<string, any> | null = null,
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
    let code: string | null = null
    let details: Record<string, any> = {}
    let body: Record<string, any> | null = null
    try {
      const parsed = await response.json()
      if (parsed && typeof parsed === 'object') {
        body = parsed as Record<string, any>
        detail = body.detail ?? body.error?.message ?? detail
        if (typeof body.error?.code === 'string') code = body.error.code
        if (body.error?.details && typeof body.error.details === 'object') {
          details = body.error.details as Record<string, any>
        }
      }
    } catch {
      /* keep default detail */
    }
    throw new ApiError(detail, response.status, code, details, body)
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
  /** 0-100. A run walks this while it executes, so the UI can say how far it is. */
  progress?: number
  /** Which step that percentage refers to, e.g. `computing metrics`. */
  current_step?: string | null
}

export interface EquityPoint {
  timestamp: string
  equity: number
  cash: number
  position_value: number
  close: number
}

/**
 * A run as it exists *before* it has a result. `POST /backtests` answers with this
 * shape: when execution is offloaded the run is still running and has no result
 * row yet, so every result field is absent rather than empty (ADR-180).
 */
export interface BacktestRun extends BacktestSummary {
  progress: number
  current_step: string | null
  error_message?: string | null
  result_hash?: string | null
  metrics?: Record<string, number | null>
  equity_curve?: EquityPoint[]
  trades?: Array<Record<string, unknown>>
  parameters?: Record<string, unknown>
  execution_model?: Record<string, unknown>
  warnings?: string[]
}

export interface BacktestDetail extends BacktestSummary {
  progress: number
  current_step: string | null
  metrics: Record<string, number | null>
  equity_curve: EquityPoint[]
  trades: Array<Record<string, unknown>>
  result_hash: string
  parameters: Record<string, unknown>
  execution_model: Record<string, unknown>
  warnings: string[]
}

/**
 * Phase C analysis of one completed run (docs/30, ADR-188): performance, risk and
 * the buy-and-hold comparison, derived server-side from the stored curve. Every
 * field the server could not compute is `null` — the page renders 「未知」, never 0.
 */
export interface AnalysisCurvePoint {
  timestamp: string | null
  equity: number
}

export interface AnalysisDerived {
  calmar: number | null
  downside_deviation: number | null
  excess_return: number | null
  final_equity_gap: number | null
  worst_bar_return: number | null
}

export interface AnalysisWorstTrade {
  pnl: number | null
  exit_time: string | null
  direction: string | null
}

export interface AnalysisRisk {
  max_drawdown: number | null
  max_drawdown_duration_bars: number | null
  max_drawdown_duration_days: number | null
  recovery_bars: number | null
  recovered: boolean | null
  recovery_text: string | null
  worst_bar_return: number | null
  worst_month_return: number | null
  worst_trade: AnalysisWorstTrade | null
  max_consecutive_losses: number | null
  downside_deviation: number | null
}

export interface AnalysisBenchmark {
  label: string
  kind: string
  source: string
  fees_included: boolean
  window_matched: boolean
  bars_matched: number
  curve: AnalysisCurvePoint[]
  total_return: number | null
  cagr: number | null
  annualized_volatility: number | null
  sharpe: number | null
  max_drawdown: number | null
  final_equity: number | null
}

export type AnalysisSampleTier = 'insufficient' | 'preliminary' | 'enough'

export interface AnalysisSample {
  trades: number
  bars: number
  years: number | null
  tier: AnalysisSampleTier
  tier_text: string
}

export interface AnalysisCaveat {
  code: string
  message: string
}

export interface BacktestAnalysis {
  run_id: number
  result_hash: string | null
  analysis_version: string
  window: { start: string | null; end: string | null; bars: number }
  performance: { stored: Record<string, number | null>; derived: AnalysisDerived }
  risk: AnalysisRisk
  benchmark: AnalysisBenchmark | null
  sample: AnalysisSample
  caveats: AnalysisCaveat[]
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
  // Phase C 的解释任务（performance_explanation）回答的是另一组问题：结论 / 原因 /
  // 风险 / 可信程度 / 下一步。信封（cached、task_id、model、cost）完全一样，
  // 所以字段在这里是可选的，而不是另造一个信封（docs/30 §8）。
  conclusion?: string
  drivers?: string[]
  confidence?: string
  next_step?: string
}

export interface ExplainResult {
  explanation: AIExplanation
  cached: boolean
  task_id: number | null
  model: string | null
  cost_usd_estimated: number
}

export interface AIModelRecord {
  id: number
  model_name: string
  capability_tier: string
  input_cost_per_mtok: number
  output_cost_per_mtok: number
  is_active: boolean
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
  models: AIModelRecord[]
}

export interface ProviderTestResult {
  ok: boolean
  detail: string
  models_found: string[]
  /** 真实数量：`/models` 全量返回，不再被 40 条截断（ADR-176）。 */
  models_total: number
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

export interface PaperAccount {
  id: number
  name: string
  /** The strategy this account is bound to, if any: attribution is per account (ADR-114). */
  strategy_id: number | null
  /**
   * The *version* (and parameters) the account was opened from. A strategy id alone
   * cannot say which parameters the paper result belongs to, so an account opened
   * from a backtest keeps both (ADR-181).
   */
  strategy_version_id?: number | null
  /** The backtest it was copied from, if it was. Never a cascade: the run may be retired. */
  backtest_run_id?: number | null
  parameters?: Record<string, unknown>
  /** Display name of the bound strategy, resolved server-side. */
  strategy_name?: string | null
  /** Money the account was funded with: deposits minus withdrawals (ADR-066). */
  net_deposits: number
  cash: number
  /**
   * Realized P&L of the closed trades. P&L is not `cash - net_deposits`: an open
   * position has spent the cash, so a full-size buy would read as -100% (ADR-124).
   */
  realized_pnl: number
  /** Open positions marked at the latest closed bar; null when no bar qualifies. */
  market_value?: number | null
  unrealized_pnl?: number | null
  total_equity?: number | null
  /** Realized + unrealized. Rates stay null while net deposits are <= 0 (ADR-066). */
  total_pnl?: number | null
  total_pnl_pct?: number | null
  /** Why a metric is missing, e.g. no closed bar to mark the position with. */
  metric_notes?: string[]
  base_currency: string
  status: string
  reset_count: number
  created_at: string
}

export interface PaperPosition {
  id: number
  account_id: number
  asset_id: number
  symbol?: string
  quantity: number
  avg_cost: number
  realized_pnl: number
  /** Close of the latest *closed* bar. Never an intraday or invented price (ADR-007). */
  mark_price?: number | null
  mark_time?: string | null
  mark_note?: string | null
  market_value?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
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

/* ------------------------------------------------------------------ *
 * AI 研究实验室（§17）
 *
 * 研究输入 → AI 理解 → 策略假设 → 策略草案 → 能力检查。这里的类型只描述
 * 服务端真正返回的形状：`run` 是运行本身，`hypothesis` 与 `draft` 各有一层
 * 包装（数据库行 + `content` 正文），能力结论在 `draft.capability_report`。
 * 草案恒为 `executable: false`：这一版没有任何东西可以执行，也没有回测结果。
 * ------------------------------------------------------------------ */

/** 一条规则/指标的来源（ADR-154 的来源门禁）。 */
export type AIResearchOrigin = 'EXPLICIT' | 'INFERRED' | 'ASSUMED' | 'UNKNOWN'

export interface AIResearchEvidence {
  source_ref: string
  locator?: string | null
  quote?: string | null
}

/** 假设里的规则与草案里的规则共用这一形状（草案多一个 `derived_from`）。 */
export interface AIResearchRule {
  id: string
  field: string
  statement: string
  origin: AIResearchOrigin
  confidence?: string
  evidence?: AIResearchEvidence[]
  parameters?: Record<string, any>
  required_capabilities?: string[]
  note?: string | null
  /** 草案规则：它来自假设里的哪一条规则；null 表示架构师自己加的。 */
  derived_from?: string | null
}

export interface AIResearchUnknown {
  field: string
  why: string
  needed_to_formalize?: boolean
}

export interface AIResearchAmbiguity {
  phrase: string
  readings?: string[]
  needs_decision?: boolean
}

export interface AIResearchAssumption {
  statement: string
  applies_to?: string[]
  reason?: string | null
}

export interface AIResearchCapabilityRequest {
  capability: string
  statement?: string | null
  reason?: string | null
  /** 模型自己的判断，只是记录：服务端从不采信（ADR-151）。 */
  claimed_supported?: boolean
}

export interface AIResearchHypothesisContent {
  strategy_name: string
  understanding: string
  rules: AIResearchRule[]
  ambiguities: AIResearchAmbiguity[]
  unknowns: AIResearchUnknown[]
  capability_requests: AIResearchCapabilityRequest[]
  assumptions: AIResearchAssumption[]
  objective?: string | null
  market?: string[]
  asset_class?: string | null
  universe?: string | null
  timeframe?: string | null
  limitations?: string[]
  confidence?: string
}

/** 一行假设：`content` 才是模型的正文。 */
export interface AIResearchHypothesis {
  hypothesis_id: number
  run_id: number
  strategy_name: string
  status: string
  confidence: string
  role?: string
  prompt_version?: string | null
  provider?: string | null
  model?: string | null
  ai_task_id?: number | null
  content: AIResearchHypothesisContent
}

export interface AIResearchMarket {
  markets?: string[]
  asset_classes?: string[]
  timeframes?: string[]
  universe?: string | null
}

export interface AIResearchIndicator {
  name: string
  origin: AIResearchOrigin
  parameters?: Record<string, any>
  note?: string | null
}

/** 草案需要、而系统目前没有的能力，也带着模型建议的替代做法。 */
export interface AIResearchNeed {
  capability: string
  affected_rule: string
  reason: string
  suggested_alternative?: string | null
  alternative_is_experimental?: boolean
}

export interface AIResearchAlternative {
  label: string
  statement: string
  what_it_gives_up?: string[]
  differs_from_original?: boolean
}

export interface AIResearchDraftContent {
  strategy_name: string
  /** 模型自报的能力结论；页面上只用服务端判定的那一份。 */
  status: string
  market: AIResearchMarket
  rules: AIResearchRule[]
  unknowns: AIResearchUnknown[]
  required_capabilities: AIResearchNeed[]
  experimental_alternatives: AIResearchAlternative[]
  indicators: AIResearchIndicator[]
  assumptions: AIResearchAssumption[]
  parameters?: Record<string, any>
  notes?: string[]
  understanding_of_original?: string | null
  /** 恒为 false：草案是给人读的，不可执行。 */
  executable: boolean
}

export interface AIResearchCapabilityItem {
  capability: string
  status: string
  reason?: string
  /** hypothesis / hypothesis_rule / indicator / draft_rule / draft */
  required_by?: string
  affected_rule?: string | null
  claimed_supported?: boolean
  overclaimed?: boolean
  suggested_alternative?: string | null
  alternative_is_experimental?: boolean
}

/** 服务端算出来的能力报告（不采信模型的自报结论）。 */
export interface AIResearchCapabilityReport {
  verdict: string
  requested: string[]
  supported: string[]
  partial: string[]
  missing: string[]
  model_capabilities: string[]
  reasons?: Record<string, string>
  items: AIResearchCapabilityItem[]
}

/** 人工确认可以给出的三种结论；后端对未知取值回 422。 */
export type AIResearchDraftDecision = 'confirmed' | 'rejected' | 'needs_revision'

/**
 * 一次人工确认的记录（Human Confirmation）。
 *
 * 它是「人看过草案并拍板」这件事本身，和 AI 的判断分开存放：`is_human_decision`
 * 恒为 true，`audit_id` 指向审计流水。它不改写草案，也不生成可执行策略版本。
 */
export interface AIResearchDraftConfirmation {
  decision: string
  note: string | null
  decided_by: string
  decided_at: string
  audit_id: number
  is_human_decision: boolean
}

/** `POST /ai/strategy/drafts/{id}/confirmations` 的 201 响应。 */
export interface AIResearchDraftConfirmationResult {
  draft_id: number
  run_id: number
  decision: string
  confirmation: AIResearchDraftConfirmation
  strategy_version_created: boolean
}

/** 一行草案：`content` 与 `capability_report` 分开返回。 */
export interface AIResearchDraft {
  draft_id: number
  run_id: number
  hypothesis_id?: number | null
  version?: number
  status: string
  capability_status?: string | null
  model_status?: string | null
  executable: boolean
  model?: string | null
  ai_task_id?: number | null
  /**
   * 这份草案编译出来的策略版本；`null` / 缺失 = 还没有编译过。
   *
   * 它是「编译过」这件事的记录，所以刷新页面、重新打开一条旧运行，都能从
   * 这个字段知道该显示版本与激活步骤，而不是再显示一次「编译」按钮。
   */
  compiled_strategy_version_id?: number | null
  content: AIResearchDraftContent
  capability_report: AIResearchCapabilityReport
  /** 最新一次人工确认；`null` / 缺失表示还没有人拍过板。 */
  confirmation?: AIResearchDraftConfirmation | null
}

/** `POST /ai/strategy/drafts/{id}/compile` 成功（201）时新建的那个策略版本。 */
export interface CompileDraftResult {
  result: string
  strategy_id: number
  strategy_version_id: number
  version: string
  compile_hash: string | null
  /** 编译器留下的完整报告（`rejections` / `slots` / `rules` / `dsl_validation`…）。 */
  report: Record<string, any>
}

/** 编译报告里的一条拒绝项（docs/29 §16.6）。 */
export interface CompileRejection {
  code: string
  slot: string
  rule_ids?: string[]
  detail?: string
  /** 这一条是否属于「必须由人来定」的那一类。 */
  user_decidable?: boolean
}

/** 编译器自己拒绝（422）时的正文：`{result, report}`，没有 `error` 信封。 */
export interface CompileDraftRefusal {
  result: string
  report: Record<string, any>
}

export interface AIResearchViolation {
  code: string
  message: string
  severity?: string
  field?: string | null
}

/** 警告不是拒绝：材料被截断、或模型写了没算过的结果数字。 */
export interface AIResearchWarning {
  kind?: string
  source_ref?: string
  kept_chars?: number
  original_chars?: number
  metric?: string
  text?: string
  note?: string
}

export interface AIResearchSourceMeta {
  source_ref: string
  kind?: string
  label?: string | null
  uri?: string | null
  parse_status?: string
  text_hash?: string
  size_bytes?: number
  characters_read?: number
  fragment_count?: number
  license_note?: string | null
}

/**
 * 一次抓取的观测记录（`POST /ai/sources/url`）。
 *
 * 服务端返回的是**描述**而不是原文：哈希、字节数/字数、解析器身份，加一段被保留的摘要
 * （ADR-161）。第三方材料默认只保留 500 字，除非调用方声明「这份材料我有权使用」
 * （`retention: 'full'` + `license_note`），即便如此也仍然有上限。
 */
export interface AISourceSnapshot {
  snapshot_id: number
  source_kind: string
  snapshot_status: string
  parse_status: string
  source_ref?: string | null
  label?: string | null
  original_uri?: string | null
  final_uri?: string | null
  status_code?: number | null
  content_type?: string | null
  bytes_read?: number
  chars_read?: number
  source_hash?: string | null
  text_hash?: string | null
  parser?: string | null
  parser_version?: string | null
  robots_ok?: boolean | null
  retention?: { policy?: string | null; retained_chars?: number; truncated?: boolean }
  excerpt?: string[]
  warnings?: Array<Record<string, any>>
  redirects?: string[]
  error_code?: string | null
  error_message?: string | null
  fetched_at?: string | null
  created_at?: string | null
}

/**
 * 交给研究运行的一段材料。
 *
 * `text` 是调用方自己贴的原文；`url`/`pdf` 可以用 `uri` 命名（服务端去抓），也可以用
 * `snapshot_id` 命名（服务端已经看过一次，只读回当时保留的摘要，不再联网）。
 * 两者都给时以 `text` 为准，运行里会记一条 warning。
 */
export interface AIResearchSourceInput {
  kind?: 'user_input' | 'text' | 'github_file' | 'url' | 'pdf'
  text?: string
  source_ref?: string
  label?: string
  uri?: string
  snapshot_id?: number
  license_note?: string
  retention?: 'excerpt' | 'full'
}

/** 一次研究运行的完整载荷（含假设、草案与能力报告）。 */
export interface AIResearchRun {
  run_id: number
  question: string
  status: string
  current_step: string
  capability_status?: string | null
  attempts?: number
  sources?: AIResearchSourceMeta[]
  warnings?: AIResearchWarning[]
  violations?: AIResearchViolation[]
  error_message?: string | null
  researcher_task_id?: number | null
  architect_task_id?: number | null
  created_at?: string | null
  completed_at?: string | null
  hypothesis?: AIResearchHypothesis | null
  draft?: AIResearchDraft | null
}

/** 列表投影：没有假设与草案正文。 */
export interface AIResearchRunSummary {
  run_id: number
  question: string
  status: string
  current_step: string
  capability_status?: string | null
  attempts?: number
  violation_count?: number
  warning_count?: number
  created_at?: string | null
  completed_at?: string | null
}

// ---- 策略实验（/experiments）：一次实验 = 一条留在服务端的记录 -------------------

/** 一次实验的跑法。服务端 `ExperimentCreate.kind` 只认这五个。 */
export type ExperimentKind = 'backtest' | 'sensitivity' | 'monte_carlo' | 'walk_forward' | 'oos'

/**
 * `POST /experiments` 的请求体（服务端 `extra="forbid"`，多发一个键就是 422）。
 *
 * 可选字段只在所选 kind 需要时才发：服务端按 `model_fields_set` 判断哪些字段是
 * 「这次跑法点名要的」，多余的默认值会改变它的判断（例如 `timeframe`）。
 */
export interface ExperimentCreatePayload {
  name: string
  kind: ExperimentKind
  strategy_version_id: number
  /**
   * `true` = 只把这次研究的配置冻结成一条草稿（status=draft），不跑任何量化代码（ADR-182）。
   *
   * **只在真的要存草稿时才发这个键**：服务端 `extra="forbid"`，而且「有没有这个键」
   * 本身就是「要不要现在跑」的意思，多发一个 `draft: false` 是无害的，但少发才是默认语义。
   */
  draft?: boolean
  notes?: string
  symbol?: string
  series_id?: number
  timeframe?: string
  start?: string
  end?: string
  parameters?: Record<string, unknown>
  grid?: Record<string, unknown[]>
  metric?: string
  backtest_run_id?: number
  runs?: number
  trades_per_run?: number
  seed?: number
  train_bars?: number
  test_bars?: number
  step?: number
  oos_pct?: number
  oos_start?: string
}

export interface ExperimentResultOut {
  id: number
  kind: string
  label: string | null
  parameters: Record<string, unknown> | null
  backtest_run_id: number | null
  metrics: Record<string, number | null> | null
  /** 引擎在这个点上的完整对象：敏感性点里的 `warmup_unmet` / `objective` / `warnings` 都在这里。 */
  payload: Record<string, unknown>
  created_at: string
}

export interface ExperimentSummaryOut {
  id: number
  name: string
  kind: string
  status: string
  strategy_version_id: number
  series_id: number | null
  symbol: string | null
  timeframe: string
  result_count: number
  backtest_run_id: number | null
  metrics: Record<string, number | null>
  created_at: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  // ---- Phase B（ADR-182/183）：以下字段可空，旧服务端不返回时按「未知」处理 ----
  /** 实验建立时冻结的初始资金；未知就是 null，不拿回测默认值冒充。 */
  initial_capital?: number | null
  /** 实验冻结的行情区间（服务端存的是那一刻的输入，不是当前策略的默认值）。 */
  start_date?: string | null
  end_date?: string | null
  updated_at?: string | null
  /** 归档时间；非 null 说明这条实验已被收纳（归档不是删除，列表默认仍包含它）。 */
  archived_at?: string | null
  strategy_id?: number | null
  strategy_name?: string | null
  version?: string | null
  /** 这条实验涉及的所有标的（一次实验可以扫多个标的）。 */
  symbols?: string[]
  /** `true` = 由一条已有的回测收养而来（ADR-183）。 */
  is_adopted?: boolean
}

/** `PATCH /experiments/{id}`：只改标签，改不了配置——状态只能经由 run/archive 迁移。 */
export interface ExperimentUpdatePayload {
  name?: string
  notes?: string
}

/** `POST /experiments/from-backtest/{run_id}`：收养一条已有回测，两个字段都可省。 */
export interface ExperimentAdoptPayload {
  name?: string
  notes?: string
}

/** 详情 = 列表投影 + 请求原文 + 引擎摘要 + 逐条结果。 */
export interface ExperimentDetailOut extends ExperimentSummaryOut {
  notes: string | null
  parameters: Record<string, unknown>
  request: Record<string, unknown>
  summary: Record<string, unknown> | null
  results: ExperimentResultOut[]
}

export interface ExperimentListOut {
  experiments: ExperimentSummaryOut[]
}

/** 对比：`metrics` 是服务端给出的指标列，`experiments` 的指标键平铺在每一行上。 */
export interface ExperimentCompareOut {
  metrics: string[]
  experiments: Array<Record<string, unknown>>
  /**
   * 服务端对「这些实验的条件是否相同」的结论（ADR-184），由**存储值**比较得出：
   * 前端只负责把它显示出来，绝不在本地重算——否则网页和 API 会给出两种结论。
   */
  comparability?: 'same-config' | 'different-config'
  /** 人类可读的差异维度，例如 `参数不同`、`标的不同`、`初始资金不同`。 */
  differences?: string[]
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
  /**
   * The same run read as "may not have a result yet". Polling uses this one: a run
   * that is still executing has no result row, and asking for `BacktestDetail`
   * would be a lie about what came back (ADR-180).
   */
  backtestRun: (id: number) => request<BacktestRun>(`/backtests/${id}`),
  /**
   * Phase C: performance, risk and the buy-and-hold comparison of a finished run.
   * Read-only and computed from the stored curve, so calling it changes nothing
   * (docs/30 §11). A run that is still executing answers 409.
   */
  backtestAnalysis: (id: number) => request<BacktestAnalysis>(`/backtests/${id}/analysis`),
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
    request<BacktestRun>('/backtests', {
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
  // One strategy version can be asked about directly (ADR-181): an account bound to a
  // version must not have to filter the newest N signals client-side, which would
  // silently drop the older ones.
  signals: (
    state?: string,
    limit = 100,
    symbol?: string,
    offset = 0,
    strategyVersionId?: number,
  ) =>
    request<SignalRecord[]>(
      `/signals?limit=${limit}&offset=${offset}${state ? `&state=${encodeURIComponent(state)}` : ''}${
        symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''
      }${strategyVersionId ? `&strategy_version_id=${strategyVersionId}` : ''}`,
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
  /** One account with its money totals (cash, market value, unrealized, total equity). */
  paperAccount: (accountId: number) => request<PaperAccount>(`/paper/accounts/${accountId}`),
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
  /**
   * Execute a signal on a paper account. `quantity` and `notional` are optional and
   * mutually exclusive: without them the account keeps sizing by `max_position_pct`,
   * with them the fill is exactly what was asked for (ADR-181).
   */
  executePaperSignal: (
    accountId: number,
    signalId: number,
    sizing: { quantity?: number; notional?: number } = {},
  ) =>
    request<PaperExecution>(`/paper/accounts/${accountId}/execute`, {
      method: 'POST',
      body: JSON.stringify({
        signal_id: signalId,
        ...(sizing.quantity != null ? { quantity: sizing.quantity } : {}),
        ...(sizing.notional != null ? { notional: sizing.notional } : {}),
      }),
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
  /**
   * Open an account. Passing `backtest_run_id` copies that run's strategy version and
   * parameters into the account, which is what makes the paper result comparable with
   * the backtest that motivated it (ADR-181).
   */
  createPaperAccount: (
    name: string,
    initialCash: number,
    binding: {
      strategyVersionId?: number
      backtestRunId?: number
      parameters?: Record<string, unknown>
    } = {},
  ) =>
    request<PaperAccount>('/paper/accounts', {
      method: 'POST',
      body: JSON.stringify({
        name,
        initial_cash: initialCash,
        ...(binding.strategyVersionId != null
          ? { strategy_version_id: binding.strategyVersionId }
          : {}),
        ...(binding.backtestRunId != null ? { backtest_run_id: binding.backtestRunId } : {}),
        ...(binding.parameters != null ? { parameters: binding.parameters } : {}),
      }),
    }),
  settings: () => request<AppSettings>('/settings'),
  updateSetting: (key: string, value: string) =>
    request<Record<string, unknown>>('/settings', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),
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
  /**
   * Phase C explanation: the plain-language reading of the analysis above. The
   * numbers do not come from here — they come from `backtestAnalysis`, and this
   * call failing only means the page shows fewer words (docs/30 §8.3).
   */
  explainPerformance: (runId: number) =>
    request<ExplainResult>(`/backtests/${runId}/explain-performance`, { method: 'POST' }),
  aiModels: () => request<{ models: Array<Record<string, any>> }>('/ai/models'),
  // 模型级开关：停用不删除，历史 AI Task / Usage 保留（ADR-173）。409 = 该模型是
  // 供应商当前唯一可用模型，后端拒绝而不是偷偷改 default_model。
  updateAiModel: (id: number, isActive: boolean) =>
    request<Record<string, any>>(`/settings/ai/models/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ is_active: isActive }),
    }),
  // 删除当前模型配置：允许即使这个模型已经产生过 AI 调用记录（ADR-177）。删掉的只是
  // 配置，历史 AI Task / Usage 保留当时的模型名称，不会被一起删除。
  deleteAiModel: (id: number) =>
    request<{ deleted: number; model_name: string }>(`/settings/ai/models/${id}`, {
      method: 'DELETE',
    }),
  aiPrompts: () => request<{ prompts: Array<Record<string, any>> }>('/ai/prompts'),
  aiTasksList: (limit = 50) => request<Array<Record<string, any>>>(`/ai/tasks?limit=${limit}`),
  // Read-only audit detail of one AI task: structured output, token usage and
  // error. Secrets are never echoed back by the API.
  aiTask: (taskId: number) => request<Record<string, any>>(`/ai/tasks/${taskId}`),
  aiUsage: (limit = 100) => request<{ usage: Array<Record<string, any>> }>(`/ai/usage?limit=${limit}`),
  // AI 研究实验室（§17）：研究输入 → AI 理解 → 策略假设 → 策略草案 → 能力检查。
  // 一次 POST 就返回整条链路的结果；没有配置 AI 提供方时后端回答 503。
  aiResearchStart: (payload: {
    question: string
    sources: AIResearchSourceInput[]
    model?: string
  }) => request<AIResearchRun>('/ai/research', { method: 'POST', body: JSON.stringify(payload) }),
  /**
   * 抓一个网页并把它记下来（v2.1.0 的 `POST /ai/sources/url`）。
   *
   * 这一步**不调用模型、不花 AI 预算**，但它是整个 API 里唯一让服务端去访问调用方指定
   * 地址的地方，所以安全判断都在服务端一层之下：守卫先拒绝地址（私网、robots、不支持的
   * 协议），再解析、再按上限保留摘要。拒绝是结果而不是崩溃：
   * 422 = 明确拒绝（`snapshot_status="blocked"`），502 = 连不上或读不懂，两者都会留下记录。
   */
  aiSourceUrl: (payload: {
    uri: string
    source_ref?: string
    label?: string
    retention?: 'excerpt' | 'full'
    license_note?: string
  }) => request<AISourceSnapshot>('/ai/sources/url', { method: 'POST', body: JSON.stringify(payload) }),
  aiResearchRuns: (limit = 20) => request<{ runs: AIResearchRunSummary[] }>(`/ai/research?limit=${limit}`),
  aiResearchRun: (runId: number) => request<AIResearchRun>(`/ai/research/${runId}`),
  // 只重跑架构师那一步，给已经存下来的假设再要一份草案。
  aiStrategyFormalize: (payload: { run_id?: number; hypothesis_id?: number; model?: string }) =>
    request<{ draft: AIResearchDraft }>('/ai/strategy/formalize', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // 人工确认：把「人看过草案、怎么决定」单独记一笔。不调用 AI，不改写草案，
  // 也不会生成可执行策略版本（`strategy_version_created` 恒为 false）。
  confirmStrategyDraft: (
    draftId: number,
    decision: AIResearchDraftDecision,
    note?: string,
  ) =>
    request<AIResearchDraftConfirmationResult>(
      `/ai/strategy/drafts/${draftId}/confirmations`,
      {
        method: 'POST',
        body: JSON.stringify({ decision, note: note?.trim() ? note.trim() : null }),
      },
    ),
  /**
   * 编译：把一份**已人工确认**的草案冻结成一个正式的策略版本（ADR-171：编译不等于
   * 激活，`make_current` 恒为 false）。
   *
   * 服务端是唯一裁判，几种拒绝都要按码处理，不能只看 message：
   * 409 `draft_not_confirmed` / `draft_already_compiled` / `version_unassignable` /
   * `version_conflict`，以及 422（编译器自己拒绝，正文是 `{result, report}`）。
   */
  compileStrategyDraft: (draftId: number, strategyId: number) =>
    request<CompileDraftResult>(`/ai/strategy/drafts/${draftId}/compile`, {
      method: 'POST',
      body: JSON.stringify({ strategy_id: strategyId }),
    }),
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
  createExperiment: (payload: ExperimentCreatePayload) =>
    request<ExperimentDetailOut>('/experiments', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  experiments: (limit = 20, strategyVersionId?: number, status?: string) =>
    request<ExperimentListOut>(
      `/experiments?limit=${limit}${
        strategyVersionId ? `&strategy_version_id=${strategyVersionId}` : ''
      }${status ? `&status=${status}` : ''}`,
    ),
  experiment: (id: number) => request<ExperimentDetailOut>(`/experiments/${id}`),
  // 草稿（或失败重跑）→ running → completed/failed；状态冲突由服务端回 409（ADR-182）。
  runExperiment: (id: number) =>
    request<ExperimentDetailOut>(`/experiments/${id}/run`, { method: 'POST' }),
  // 归档 = 收纳：实验仍在列表里，只是标成 archived，随时还能回读（ADR-182）。
  archiveExperiment: (id: number) =>
    request<ExperimentDetailOut>(`/experiments/${id}/archive`, { method: 'POST' }),
  // 只能改名称与备注：配置在实验建立那一刻就冻结了（ADR-182）。
  patchExperiment: (id: number, payload: ExperimentUpdatePayload) =>
    request<ExperimentDetailOut>(`/experiments/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  // 把一条已有回测收养成实验：结果逐字复制该 run 已存的值，绝不重算（ADR-183）。
  experimentFromBacktest: (runId: number, payload: ExperimentAdoptPayload = {}) =>
    request<ExperimentDetailOut>(`/experiments/from-backtest/${runId}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  compareExperiments: (ids: number[]) =>
    request<ExperimentCompareOut>(`/experiments/compare?ids=${ids.join(',')}`),
  // 204 无正文：删掉的只是这条实验记录，它产生的 BacktestRun 是另一个产物。
  deleteExperiment: (id: number) => request<void>(`/experiments/${id}`, { method: 'DELETE' }),
  updateAiProvider: (id: number, payload: Record<string, unknown>) =>
    request<AIProviderRecord>(`/settings/ai/providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  // 保存「发现 + 手动添加」的模型选择：勾选的进目录并置为 active，取消勾选的只置为
  // inactive（从不删除），全新模型只有被勾选才会创建（ADR-176）。
  saveAiProviderModels: (
    id: number,
    payload: { models?: Array<Record<string, unknown>>; manual_models?: string[] },
  ) =>
    request<AIProviderRecord>(`/settings/ai/providers/${id}/models`, {
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
}
