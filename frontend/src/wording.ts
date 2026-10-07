// 人话词汇表（ADR-127）.
//
// The engine speaks in enums: BUY/SELL/WAIT/NO_SIGNAL, lifecycle stages, raw
// metric keys. None of that is wrong — it is simply not a sentence a reader who
// does not program can act on. The audit asked for one thing above all: the
// professional term may stay, but the first time it appears the interface has to
// say what it is for. That translation lives here and only here, so a term cannot
// drift into three different phrasings across three pages.

/** Signal states, said as what they mean for a position — not what they are (audit §14). */
export const SIGNAL_LABELS: Record<string, string> = {
  BUY: '看多信号',
  SELL: '看空 / 退出信号',
  WAIT: '暂不确认',
  NO_SIGNAL: '暂无信号',
}

export function signalLabel(state: string | null | undefined): string {
  if (!state) return '—'
  return SIGNAL_LABELS[state] ?? state
}

/** The wording that must appear next to any signal, anywhere (audit §14). */
export const SIGNAL_DISCLAIMER = '这是策略研究信号，不是自动交易指令。'
export const REFERENCE_PRICE_DISCLAIMER = '这是策略模型计算出的参考值，不代表未来价格预测。'

/** Research lifecycle stages. Moved here from StrategiesView so both pages read one table. */
export const STAGE_LABELS: Record<string, string> = {
  imported: '已导入',
  normalized: '已规范化',
  validated: '已校验',
  backtested: '已回测',
  oos_tested: '已做样本外',
  paper_trading: '模拟盘',
  reference_signal: '参考信号',
  degraded: '已降级',
  retired: '已退役',
}

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return '—'
  return STAGE_LABELS[stage] ?? stage
}

/** What the next lifecycle stage actually asks a person to do (audit §5, §9). */
const NEXT_STEPS: Record<string, string> = {
  normalized: '把这个策略补全成一个可校验的版本',
  validated: '运行一次历史回测，看它过去的表现',
  backtested: '做样本外验证：用没参与调参的数据再测一次',
  oos_tested: '放进模拟盘跑一段时间，看真实成交环境下是什么样',
  paper_trading: '在模拟盘里积累足够多的交易，再和回测结果比较',
  reference_signal: '继续观察信号，把它当作研究信息而不是交易指令',
  degraded: '先查清降级原因，再决定要不要继续推进',
  retired: '不用再推进：这个策略已经退役',
}

export function nextStepText(stage: string | null | undefined): string {
  if (!stage) return '还没有可推进的下一步：先创建或导入一个策略'
  return NEXT_STEPS[stage] ?? `推进到「${stageLabel(stage)}」`
}

/** Where that next step is actually carried out, so the home page can link to it. */
const STAGE_PAGES: Record<string, { to: string; label: string }> = {
  imported: { to: '/strategies', label: '去「我的策略」' },
  normalized: { to: '/strategies', label: '去「我的策略」' },
  validated: { to: '/backtest', label: '去「回测」' },
  backtested: { to: '/backtest', label: '去「回测」' },
  oos_tested: { to: '/paper', label: '去「模拟验证」' },
  paper_trading: { to: '/paper', label: '去「模拟验证」' },
  reference_signal: { to: '/signals', label: '去「信号」' },
}

export function stagePage(stage: string | null | undefined): { to: string; label: string } | null {
  if (!stage) return null
  return STAGE_PAGES[stage] ?? null
}

/** Timeframes: `1d` is a database value, 日线 is what a person says. */
export const TIMEFRAME_LABELS: Record<string, string> = {
  '1d': '日线',
  '1h': '小时线',
  '4h': '4 小时线',
  '1w': '周线',
}

export function timeframeLabel(timeframe: string | null | undefined): string {
  if (!timeframe) return ''
  return TIMEFRAME_LABELS[timeframe] ?? timeframe
}

/** Data-quality enums, in plain words (the raw value stays for the advanced view). */
export const QUALITY_LABELS: Record<string, string> = {
  valid: '数据正常',
  invalid: '数据有问题',
  stale: '数据偏旧',
  partial: '数据不完整',
  unknown: '还没有数据',
}

export function qualityLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return QUALITY_LABELS[status] ?? status
}

/**
 * Paper-account states (review report P0-2): `active` / `closed` are database
 * values. A reader who does not program should not have to learn them.
 */
export const ACCOUNT_STATUS_LABELS: Record<string, string> = {
  active: '运行中',
  inactive: '已暂停',
  closed: '已关闭',
}

export function accountStatusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return ACCOUNT_STATUS_LABELS[status] ?? status
}

/** Where a persisted signal stands in the operator's own workflow. */
export const SIGNAL_STATUS_LABELS: Record<string, string> = {
  pending: '未确认',
  acknowledged: '已确认',
}

export function signalStatusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return SIGNAL_STATUS_LABELS[status] ?? status
}

/** How a signal's own outcome turned out, once the bar it pointed at has closed. */
export const OUTCOME_LABELS: Record<string, string> = {
  pending: '还没走完',
  profitable: '这次赚钱了',
  unprofitable: '这次亏钱了',
}

export function outcomeLabel(state: string | null | undefined): string {
  if (!state) return '—'
  return OUTCOME_LABELS[state] ?? state
}

/**
 * Which market data source is configured, in one plain sentence (review report P1-7).
 *
 * The default symbol depends on it: the demo provider only serves `DEMO-AAPL` and
 * `DEMO-BTC`, so leaving `DEMO-AAPL` in the box under a real provider guarantees an
 * empty series. Both the data page and the backtest page ask this instead of guessing.
 */
export const PROVIDER_LABELS: Record<string, string> = {
  synthetic: '演示数据（只服务 DEMO-AAPL、DEMO-BTC 两个代码）',
  yahoo_finance: '雅虎财经（真实日线行情）',
}

export function providerLabel(provider: string | null | undefined): string {
  if (!provider) return '还没有读到行情源'
  return PROVIDER_LABELS[provider] ?? provider
}

/** The symbol worth suggesting for this provider, before the reader types their own. */
export function defaultSymbolFor(provider: string | null | undefined): string {
  return provider === 'synthetic' ? 'DEMO-AAPL' : 'AAPL'
}

/**
 * Whether a strategy version passed its own validation (review report P0-2).
 *
 * The engine stores `pending` / `valid` / `invalid` (`backend/app/data/strategy_service.py:190`
 * writes the last two), and those three words used to be printed verbatim in the
 * version dropdowns. Plain mode now says them in Chinese; the raw value stays
 * visible in advanced mode.
 */
export const VALIDATION_LABELS: Record<string, string> = {
  pending: '还没有校验',
  valid: '已通过校验',
  invalid: '没有通过校验',
}

export function validationLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return VALIDATION_LABELS[status] ?? status
}

/**
 * Engine metric keys, as a reader would name them.
 *
 * `指标明细` used to print `annualized_volatility` and `max_consecutive_losses`
 * verbatim, which is an engine vocabulary, not a report (audit §12, §20). The
 * alias is the one-line "what is it for" that a professional term gets on first
 * sight; it is optional because some entries are already plain Chinese.
 */
interface MetricWording {
  label: string
  alias?: string
}

const METRIC_WORDING: Record<string, MetricWording> = {
  total_return: { label: '总收益率' },
  cagr: { label: '年化收益', alias: '按年折算的复合增长' },
  final_equity: { label: '期末权益' },
  initial_capital: { label: '初始资金' },
  max_drawdown: { label: '最大回撤' },
  annualized_volatility: { label: '年化波动率', alias: '收益的起伏程度' },
  max_drawdown_duration_bars: { label: '最长回撤持续（根）' },
  sharpe: { label: '夏普比率', alias: '收益 / 波动效率' },
  sortino: { label: '索提诺比率', alias: '收益 / 下跌风险效率' },
  number_of_trades: { label: '交易次数' },
  win_rate: { label: '胜率' },
  avg_win: { label: '平均每次盈利' },
  avg_loss: { label: '平均每次亏损' },
  profit_factor: { label: '盈亏效率', alias: '赚的钱是亏的钱的几倍' },
  expectancy: { label: '每笔交易期望收益', alias: '平均每笔交易的期望结果' },
  total_fees: { label: '手续费' },
  average_holding_bars: { label: '平均持仓（根）' },
  max_consecutive_losses: { label: '最长连续亏损' },
  exposure: { label: '持仓时间占比', alias: '实际持有仓位的时间比例' },
  turnover: { label: '换手率' },
}

/** Aliases for labels that are already Chinese (they have no engine key to hang off). */
const LABEL_ALIASES: Record<string, string> = {
  组合夏普: '收益 / 波动效率（组合）',
  组合总收益: '组合一共赚了多少',
  组合最大回撤: '组合最难熬的一段跌幅',
}

export function metricKeyLabel(key: string): string {
  return METRIC_WORDING[key]?.label ?? key
}

export function termAlias(label: string): string {
  const fromKey = Object.values(METRIC_WORDING).find((entry) => entry.label === label)?.alias
  return fromKey ?? LABEL_ALIASES[label] ?? ''
}
