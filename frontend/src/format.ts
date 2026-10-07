export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A'
  return `${(value * 100).toFixed(digits)}%`
}

export function toneOf(value: number | null | undefined): 'pos' | 'neg' | 'plain' {
  if (value === null || value === undefined || Number.isNaN(value)) return 'plain'
  if (value > 0) return 'pos'
  if (value < 0) return 'neg'
  return 'plain'
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

/**
 * Engine metrics the API returns as fractions (0.0512 is 5.12%), so they need
 * `formatPercent` (ADR-087): `total_return` and `sharpe` sit in one row and are
 * not the same unit. These tables are rendered by both BacktestView and the Lab's
 * experiment panel, so the rule lives here instead of being re-typed at each call
 * site.
 */
const RATIO_METRICS = new Set([
  'total_return',
  'cagr',
  'max_drawdown',
  'win_rate',
  'annualized_volatility',
  'exposure',
])

/** Money and per-trade money: four decimals of a dollar amount are noise. */
const MONEY_METRICS = new Set([
  'final_equity',
  'initial_capital',
  'avg_win',
  'avg_loss',
  'expectancy',
])

/**
 * Counts come back as floats (`2.0` trades) and read as noise that way; the
 * engine's integer fields are the ones a reader compares by eye.
 */
const COUNT_METRICS = new Set([
  'number_of_trades',
  'max_consecutive_losses',
  'max_drawdown_duration_bars',
])

export function formatMetric(key: string, value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  // A percentage needs two decimals, not four: the extra digits are noise.
  if (RATIO_METRICS.has(key)) return formatPercent(value, 2)
  if (MONEY_METRICS.has(key)) return formatNumber(value, 2)
  if (COUNT_METRICS.has(key)) return formatNumber(value, 0)
  if (key === 'average_holding_bars') return formatNumber(value, 1)
  return formatNumber(value, digits)
}

/**
 * P&L of a paper account as a fraction of the money it was funded with.
 *
 * `realizedPnl` is the account's realized P&L, not `cash - netDeposits`: a position that
 * is still open has spent the cash, so a full-size buy would show as -100% (ADR-124).
 * `null` when there is no positive net-deposit baseline to divide by: an account
 * withdrawn down to (or past) its deposits has no meaningful return percentage, and
 * printing `-100%`/`NaN%` there would read as a trading loss (ADR-066).
 */
export function paperPnlPct(
  netDeposits: number | null | undefined,
  realizedPnl: number | null | undefined,
): number | null {
  if (netDeposits === null || netDeposits === undefined) return null
  if (realizedPnl === null || realizedPnl === undefined) return null
  if (!(netDeposits > 0)) return null
  return realizedPnl / netDeposits
}

export function formatPaperPnlPct(
  netDeposits: number | null | undefined,
  realizedPnl: number | null | undefined,
): string {
  const pct = paperPnlPct(netDeposits, realizedPnl)
  return pct === null ? '—' : formatPercent(pct)
}

/**
 * Direction of a signal event, in the words the panel uses.
 *
 * A closing signal names what it closes: its own `direction` is `FLAT`, which
 * reads as "no direction" and hides whether the long or the short was closed
 * (ADR-115).
 */
export function signalDirection(
  direction: string | null | undefined,
  closesDirection?: string | null,
): string {
  const closes = (closesDirection ?? '').toUpperCase()
  if (closes === 'LONG') return '平多'
  if (closes === 'SHORT') return '平空'
  switch ((direction ?? '').toUpperCase()) {
    case 'LONG':
      return '做多'
    case 'SHORT':
      return '做空'
    case 'FLAT':
      return '—'
    default:
      return direction || '—'
  }
}
