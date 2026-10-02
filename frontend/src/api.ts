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

export interface GithubAnalysis {
  owner: string
  repo: string
  ref: string
  description: string | null
  license: string | null
  files_scanned: string[]
  files_skipped: string[]
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
  validation_status: string
  immutable_hash: string
  warnings: Array<Record<string, unknown>>
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

export interface PaperAccount {
  id: number
  name: string
  initial_cash: number
  cash: number
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
  strategyVersions: (id: number) => request<StrategyVersion[]>(`/strategies/${id}/versions`),
  activateVersion: (versionId: number) =>
    request<StrategyVersion>(`/strategy-versions/${versionId}/activate`, { method: 'PUT' }),
  strategyLineage: (strategyId: number) =>
    request<Record<string, any>>(`/strategies/${strategyId}/lineage`),
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
    request<{ is_valid: boolean; issues: Array<Record<string, unknown>> }>('/strategies/validate', {
      method: 'POST',
      body: JSON.stringify(dsl),
    }),
  syncMarketData: (symbol: string, timeframe = '1d', lookbackDays = 400) =>
    request<Record<string, unknown>>('/market-data/sync', {
      method: 'POST',
      body: JSON.stringify({ symbol, timeframe, lookback_days: lookbackDays }),
    }),
  deleteSeries: (seriesId: number) =>
    request<{ deleted: number; symbol: string }>(`/market-data/series/${seriesId}`, {
      method: 'DELETE',
    }),
  series: () => request<Array<Record<string, unknown>>>('/market-data/series'),
  latestBars: (symbol: string, timeframe = '1d', limit = 120) =>
    request<{ bars: Array<{ timestamp: string; close: number; high: number; low: number; open: number }> }>(
      `/market-data/latest/${encodeURIComponent(symbol)}?timeframe=${timeframe}&limit=${limit}`,
    ),
  backtests: (strategyVersionId?: number) =>
    request<BacktestSummary[]>(
      `/backtests${strategyVersionId ? `?strategy_version_id=${strategyVersionId}` : ''}`,
    ),
  backtest: (id: number) => request<BacktestDetail>(`/backtests/${id}`),
  compareBacktests: (ids: number[]) =>
    request<{ metrics: string[]; runs: Array<Record<string, any>> }>(
      `/backtests/compare?ids=${ids.join(',')}`,
    ),
  runBacktest: (strategyVersionId: number, symbol: string, timeframe = '1d') =>
    request<BacktestDetail>('/backtests', {
      method: 'POST',
      body: JSON.stringify({ strategy_version_id: strategyVersionId, symbol, timeframe }),
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
  signals: (state?: string, limit = 100, symbol?: string, offset = 0) =>
    request<SignalRecord[]>(
      `/signals?limit=${limit}&offset=${offset}${state ? `&state=${encodeURIComponent(state)}` : ''}${
        symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''
      }`,
    ),
  acknowledgeSignal: (id: number) =>
    request<Record<string, unknown>>(`/signals/${id}/acknowledge`, { method: 'POST' }),
  signalEvidence: (id: number) => request<Record<string, any>>(`/signals/${id}/evidence`),
  signalOutcomes: (limit = 50, symbol?: string) =>
    request<Array<Record<string, any>>>(
      `/signals/outcomes?limit=${limit}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ''}`,
    ),
  signalOutcomeSummary: () =>
    request<{ evaluated: number; groups: Record<string, Record<string, any>> }>(
      '/signals/outcome-summary',
    ),
  scanSignals: () =>
    request<{ evaluated: number; created: number; signals: SignalIntent[]; disclaimer: string }>(
      '/signals/scan',
      { method: 'POST' },
    ),
  paperAccounts: () => request<PaperAccount[]>('/paper/accounts'),
  paperPerformance: (accountId: number) =>
    request<Record<string, any>>(`/paper/accounts/${accountId}/performance`),
  paperPositions: (accountId: number) =>
    request<PaperPosition[]>(`/paper/accounts/${accountId}/positions`),
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
  settings: () => request<Record<string, unknown>>('/settings'),
  updateSetting: (key: string, value: string) =>
    request<Record<string, unknown>>('/settings', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),
  audit: () => request<{ total: number; events: Array<Record<string, unknown>> }>('/settings/audit'),
  analyzeGithubRepo: (repoUrl: string, ref?: string, token?: string, maxFiles = 12) =>
    request<GithubAnalysis>('/importer/github/analyze', {
      method: 'POST',
      body: JSON.stringify({
        repo_url: repoUrl,
        ref: ref || undefined,
        token: token || undefined,
        max_files: maxFiles,
      }),
    }),
  githubSources: () => request<Array<Record<string, any>>>('/importer/github/sources'),
  githubCheckSource: (id: number) =>
    request<{ has_update: boolean; head: string | null; current_commit: string | null }>(
      `/importer/github/sources/${id}/check`,
    ),
  importGithubStrategy: (repoUrl: string, name: string, version: string, dsl: Record<string, unknown>, ref?: string) =>
    request<GithubImportResult>('/importer/github/import', {
      method: 'POST',
      body: JSON.stringify({ repo_url: repoUrl, name, version, dsl, ref: ref || undefined }),
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
  aiTasks: (limit = 50) => request<Array<Record<string, unknown>>>(`/ai/tasks?limit=${limit}`),
  aiModels: () => request<{ models: Array<Record<string, any>> }>('/ai/models'),
  aiPrompts: () => request<{ prompts: Array<Record<string, any>> }>('/ai/prompts'),
  aiTasksList: (limit = 50) => request<Array<Record<string, any>>>(`/ai/tasks?limit=${limit}`),
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
