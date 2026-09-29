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
