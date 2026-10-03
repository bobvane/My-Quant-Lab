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
