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

export const api = {
  health: () => request<HealthResponse>('/health'),
  systemInfo: () => request<SystemInfo>('/system/info'),
  assets: () => request<Asset[]>('/assets'),
  strategies: () => request<Strategy[]>('/strategies'),
  strategyVersions: (id: number) => request<StrategyVersion[]>(`/strategies/${id}/versions`),
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
  series: () => request<Array<Record<string, unknown>>>('/market-data/series'),
  latestBars: (symbol: string, timeframe = '1d', limit = 120) =>
    request<{ bars: Array<{ timestamp: string; close: number; high: number; low: number; open: number }> }>(
      `/market-data/latest/${encodeURIComponent(symbol)}?timeframe=${timeframe}&limit=${limit}`,
    ),
  backtests: () => request<BacktestSummary[]>('/backtests'),
  backtest: (id: number) => request<BacktestDetail>(`/backtests/${id}`),
  runBacktest: (strategyVersionId: number, symbol: string, timeframe = '1d') =>
    request<BacktestDetail>('/backtests', {
      method: 'POST',
      body: JSON.stringify({ strategy_version_id: strategyVersionId, symbol, timeframe }),
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
  scanSignals: () =>
    request<{ evaluated: number; created: number; signals: SignalIntent[]; disclaimer: string }>(
      '/signals/scan',
      { method: 'POST' },
    ),
  paperAccounts: () => request<PaperAccount[]>('/paper/accounts'),
  createPaperAccount: (name: string, initialCash: number) =>
    request<PaperAccount>('/paper/accounts', {
      method: 'POST',
      body: JSON.stringify({ name, initial_cash: initialCash }),
    }),
  settings: () => request<Record<string, unknown>>('/settings'),
  audit: () => request<{ total: number; events: Array<Record<string, unknown>> }>('/settings/audit'),
}
