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
 * `null` when there is no positive net-deposit baseline to divide by: an account
 * withdrawn down to (or past) its deposits has no meaningful return percentage,
 * and printing `-100%`/`NaN%` there would read as a trading loss (ADR-066).
 */
export function paperPnlPct(
  netDeposits: number | null | undefined,
  cash: number | null | undefined,
): number | null {
  if (netDeposits === null || netDeposits === undefined) return null
  if (cash === null || cash === undefined) return null
  if (!(netDeposits > 0)) return null
  return (cash - netDeposits) / netDeposits
}

export function formatPaperPnlPct(
  netDeposits: number | null | undefined,
  cash: number | null | undefined,
): string {
  const pct = paperPnlPct(netDeposits, cash)
  return pct === null ? '—' : formatPercent(pct)
}
