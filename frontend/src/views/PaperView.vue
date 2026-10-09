<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  ApiError,
  api,
  type BacktestDetail,
  type BacktestSummary,
  type PaperAccount,
  type PaperExecution,
  type PaperPosition,
  type SignalRecord,
  type Strategy,
  type StrategyVersion,
} from '@/api'
import EquityChart from '@/components/EquityChart.vue'
import StatCard from '@/components/StatCard.vue'
import { isAdvanced } from '@/mode'
import {
  accountStatusLabel,
  signalLabel,
  signalStatusLabel,
  timeframeLabel,
} from '@/wording'
import { formatDateTime, formatNumber, formatPaperPnlPct, formatPercent, signalDirection, toneOf } from '@/format'

/**
 * 模拟账户是 Phase A 主链路的最后一环：策略实验 → 回测 → 结果 → 模拟账户。
 *
 * 这一页回答两件事：① 这个账户现在值多少（钱怎么来的、哪些数字算不出来）；② 这条
 * 策略在模拟盘里跑得怎么样（和当初那次回测并排看）。三条硬规则写在显示层里：
 * - 拿钱不是赚钱：入金/提现只移动基准，永远不算收益（ADR-066）。
 * - 盈亏不从现金倒推：`cash - net_deposits` 只在账户空仓时等于盈亏（ADR-124）。
 * - 算不出来的数字写「未知」并给原因，绝不写 0，也绝不编价格（ADR-007/ADR-023）。
 */

/** `GET /paper/accounts/{id}/performance` 的真实形状：`metrics` 可能整块缺席。 */
interface PaperPerformance {
  account_id: number
  net_deposits: number
  final_equity: number
  closed_trades: number
  metrics?: Record<string, number | null>
  metric_notes?: string[]
  note?: string
}

/** `GET /paper/trades` 的一行。接口层的类型是宽泛的字典，这里收到本地形状再用。 */
interface PaperTradeRow {
  id: number
  account_id: number
  asset_id: number
  direction: string | null
  entry_time: string | null
  entry_price: number | null
  exit_time: string | null
  exit_price: number | null
  quantity: number | null
  fees: number | null
  slippage: number | null
  pnl: number | null
  r_multiple: number | null
  reason: string | null
  strategy_version: string | null
  /** 产生这一笔的成交单；`null` 表示这笔成交没有对应的成交单。 */
  order_id: number | null
  /** 成交单是为哪条信号下的；`null` 表示这笔成交不是从信号来的（ADR-204）。 */
  signal_id: number | null
}

/** 新账户的绑定方式：不绑定 / 从一次已完成的回测创建 / 直接绑定策略版本。 */
type BindMode = 'none' | 'backtest' | 'version'

const route = useRoute()
const router = useRouter()

const accounts = ref<PaperAccount[]>([])
const error = ref('')
const info = ref('')
const name = ref('PA Strategy')
const cash = ref(100000)
const creating = ref(false)

const bindMode = ref<BindMode>('none')
const backtests = ref<BacktestSummary[]>([])
const backtestsLoaded = ref(false)
const loadingBacktests = ref(false)
const selectedBacktestId = ref<number | null>(null)
const strategies = ref<Strategy[]>([])
const allVersions = ref<StrategyVersion[]>([])
const referenceLoaded = ref(false)
const selectedStrategyId = ref<number | null>(null)
const versionOptions = ref<StrategyVersion[]>([])
const loadingVersions = ref(false)
const selectedVersionId = ref<number | null>(null)

const signalId = ref<number | null>(null)
const fundAmount = ref(1000)
const positions = ref<PaperPosition[]>([])
const activeAccount = ref<number | null>(null)
const busy = ref<number | null>(null)
const performance = ref<PaperPerformance | null>(null)
const perfAccount = ref<number | null>(null)
const loadingPerf = ref<number | null>(null)

const equityCurve = ref<Array<{ timestamp: string; equity: number }>>([])
const equityNote = ref('')
const equityAccount = ref<number | null>(null)
const loadingEquity = ref<number | null>(null)

const signalPool = ref<SignalRecord[]>([])
// 绑定策略版本的账户问服务端要的就是该版本的信号（ADR-181）；scopedVersion 记住这一批是
// 哪个版本的，免得把一个版本的信号当成另一个版本的。
const scopedSignals = ref<SignalRecord[]>([])
const scopedVersion = ref<number | null>(null)
const loadingSignals = ref(false)

// 选中的账户（`?account=` 是它的唯一来源）与它的明细。
const selectedAccountId = ref<number | null>(null)
const trades = ref<PaperTradeRow[]>([])
const tradesAccount = ref<number | null>(null)
const loadingTrades = ref<number | null>(null)
const assetSymbols = ref<Record<number, string>>({})

// 执行面板。
const selectedSignalId = ref<number | null>(null)
const sizingQuantity = ref<number | null>(null)
const sizingNotional = ref<number | null>(null)
const lastFill = ref<PaperExecution | null>(null)
const fillAccount = ref<number | null>(null)

// 从「信号」页带过来的信号（`?signal_id=`，ADR-203）：读出来是为了能说出它是谁。
const handedSignal = ref<SignalRecord | null>(null)
// 服务端已经没有这条信号了（404）—— 与「读不到但可能还在」分开说。
const handedSignalGone = ref(false)
// 这一页替不替你选账户：只有「绑定这一版的账户恰好只有一个」时才选，且要说出来。
const handedAccountPicked = ref(false)

/** 正数才算填了仓位：空字符串、0、负数一律当作「没填」。 */
function positiveNumber(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

/** 数字缺失时写「未知」：0 和「不知道」是两件事（ADR-066/ADR-124）。 */
function moneyOrUnknown(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '未知' : formatNumber(value)
}

function pctOrUnknown(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '未知' : formatPercent(value)
}

/** `?account=3` → 3；没有、不是正整数都当作「没有选中」（不猜）。 */
function routeAccountId(): number | null {
  const raw = route.query.account
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value !== 'string' || value.trim().length === 0) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

/** 地址里那个正整数参数（`?signal_id=` / `?strategy_version_id=`）；读不出就是「没写」。 */
function routePositiveInt(name: 'signal_id' | 'strategy_version_id'): number | null {
  const raw = route.query[name]
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value !== 'string' || value.trim().length === 0) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

/**
 * 从「信号」页带过来的那条信号（ADR-203）。
 *
 * 信号页的「去纸面执行」把 `?signal_id=<id>` 写进地址，这一页照着它把要执行的信号填好。
 * 它**不会**自动成交：执行仍然要人按一次按钮（红线：不自动交易）。
 */
function routeSignalId(): number | null {
  return routePositiveInt('signal_id')
}

/** 带过来的信号属于哪一版；信号本身读得到时以信号自己的版本为准。 */
function routeVersionId(): number | null {
  return routePositiveInt('strategy_version_id')
}

function strategyName(strategyId: number | null | undefined): string {
  if (strategyId === null || strategyId === undefined) return '策略'
  const found = strategies.value.find((s) => s.id === strategyId)
  return found ? found.name : `策略 #${strategyId}`
}

function versionLabel(versionId: number | null | undefined): string {
  if (versionId === null || versionId === undefined) return ''
  const found = allVersions.value.find((v) => v.id === versionId)
  return found ? `v${found.version}` : `版本 #${versionId}`
}

function versionOwner(versionId: number): number | null {
  const found = allVersions.value.find((v) => v.id === versionId)
  return found ? found.strategy_id : null
}

/** 账户绑定的是什么：策略名 + 版本，以及它是从哪一次回测开出来的。 */
function bindingLabel(account: PaperAccount): string {
  const parts: string[] = []
  const versionId = account.strategy_version_id ?? null
  const owner = versionId === null ? null : versionOwner(versionId)
  const named = account.strategy_name ?? (owner === null ? null : strategyName(owner))
  if (versionId !== null) {
    parts.push(`${named ?? `策略版本 #${versionId}`} ${versionLabel(versionId)}`.trim())
  } else if (account.strategy_id !== null) {
    parts.push(`${account.strategy_name ?? strategyName(account.strategy_id)}（未记版本）`)
  }
  if (account.backtest_run_id !== null && account.backtest_run_id !== undefined) {
    parts.push(`来自回测 #${account.backtest_run_id}`)
  }
  return parts.length > 0 ? parts.join(' · ') : '不绑定'
}

/** 回测下拉项：策略名/版本、标的、周期、时间、收益率，缺什么就说什么。 */
function backtestLabel(run: BacktestSummary): string {
  const owner = versionOwner(run.strategy_version_id)
  const who =
    owner === null
      ? `策略版本 #${run.strategy_version_id}`
      : `${strategyName(owner)} ${versionLabel(run.strategy_version_id)}`
  const market = `${run.symbol ?? '标的未知'} ${run.timeframe ? timeframeLabel(run.timeframe) : '周期未知'}`
  const result = run.total_return === null ? '收益率未知' : `收益率 ${formatPercent(run.total_return)}`
  return `#${run.id} · ${who} · ${market} · ${formatDateTime(run.created_at)} · ${result}`
}

/** 信号下拉项：一眼能看出这条信号是对谁、什么时候、什么方向、什么参考价。 */
function signalOptionLabel(signal: SignalRecord): string {
  const price =
    signal.price_reference === null || signal.price_reference === undefined
      ? '参考价未知'
      : `参考价 ${formatNumber(signal.price_reference)}`
  const direction = signalDirection(signal.direction, signal.closes_direction)
  return `#${signal.id} · ${formatDateTime(signal.generated_at)} · ${signal.symbol ?? '标的未知'} ${timeframeLabel(
    signal.timeframe,
  )} · ${direction} · ${signalLabel(signal.state)} · ${price}`
}

/** 持仓没有可用的标记价时，为什么没有：优先用服务端给的原文（ADR-007/ADR-023）。 */
function markReason(position: PaperPosition): string {
  const note = position.mark_note
  return note && note.trim().length > 0
    ? note
    : '没有可用于标记的最新已收盘 K 线，所以市值与未实现盈亏都算不出来。'
}

/** 资产显示成符号；服务端没给符号、本地也没读到，就退回编号（编号不是错，编符号才是错）。 */
function assetLabel(assetId: number, symbol?: string | null): string {
  if (symbol && symbol.length > 0) return symbol
  const known = assetSymbols.value[assetId]
  return known && known.length > 0 ? known : `#${assetId}`
}

const canCreate = computed(() => name.value.trim().length > 0 && positiveNumber(cash.value) !== null)

/** 创建前把「将绑定什么」写出来，而不是让用户从下拉里猜。 */
const pendingBinding = computed(() => {
  if (bindMode.value === 'backtest') {
    const run = backtests.value.find((r) => r.id === selectedBacktestId.value)
    if (!run) return ''
    const owner = versionOwner(run.strategy_version_id)
    const who =
      owner === null
        ? `策略版本 #${run.strategy_version_id}`
        : `${strategyName(owner)} ${versionLabel(run.strategy_version_id)}`
    const market = `${run.symbol ?? '标的未知'} ${run.timeframe ? timeframeLabel(run.timeframe) : '周期未知'}`
    return `将绑定：${who} · ${market}`
  }
  if (bindMode.value === 'version') {
    const version = versionOptions.value.find((v) => v.id === selectedVersionId.value)
    if (!version) return ''
    return `将绑定：${strategyName(version.strategy_id)} ${versionLabel(version.id)}`
  }
  return ''
})

const selectedAccount = computed(
  () => accounts.value.find((a) => a.id === selectedAccountId.value) ?? null,
)

/** 账户总览上服务端的原话：为什么某项指标没发布（ADR-066）。 */
const summaryNotes = computed(() => {
  const notes = selectedAccount.value?.metric_notes ?? []
  return notes.filter((note) => typeof note === 'string' && note.trim().length > 0).join('；')
})

/** 总资产必须等于「现金 + 持仓市值」；对不上就说出来，而不是挑一个数字显示。 */
const equityIdentityMismatch = computed(() => {
  const account = selectedAccount.value
  if (!account) return false
  const total = account.total_equity
  const market = account.market_value
  if (total === null || total === undefined) return false
  if (market === null || market === undefined) return false
  return Math.abs(total - (account.cash + market)) > 0.005
})

const recentSignals = computed(() => signalPool.value.slice(0, 10))

/**
 * 信号按账户绑定的策略版本筛。
 *
 * 服务端 `GET /signals` 支持 `strategy_version_id`（ADR-181），接口层也已暴露这个参数，
 * 所以绑定版本的账户走**服务端筛选**（见 `loadSignals`），不再取最近一批回来在本地筛——
 * 本地筛会静默漏掉比这一批更旧的信号。未绑定版本的账户才用不过滤版本的那一批。
 */
const panelSignals = computed(() => {
  const version = selectedAccount.value?.strategy_version_id ?? null
  if (version === null) return signalPool.value
  // 绑定了版本但还没读到该版本的信号时宁可空着，也不拿别的版本的信号冒充。
  return scopedVersion.value === version ? scopedSignals.value : []
})

const selectedSignal = computed(
  () => panelSignals.value.find((signal) => signal.id === selectedSignalId.value) ?? null,
)

// ---- 从「信号」页带过来的信号（`?signal_id=`，ADR-203）-------------------------
//
// 信号页只写地址，不写状态：这一页照着地址把「要执行哪一条」填好，但**不成交**。
// 成交永远要人按一次「执行」（红线：不自动交易）。

/** 带过来的信号属于哪一版：信号自己读得到就听它的，读不到才退回地址里那个版本号。 */
const handedVersion = computed<number | null>(
  () => handedSignal.value?.strategy_version_id ?? routeVersionId(),
)

/** 绑定这一版的账户：一个都没有时不猜，有两个以上时也不猜（谁也不比谁更该被选中）。 */
const handedAccounts = computed<PaperAccount[]>(() => {
  const version = handedVersion.value
  if (version === null) return []
  return accounts.value.filter((account) => account.strategy_version_id === version)
})

/**
 * 读一次地址里点名的信号，并把「按信号 ID 执行」的输入框也填上。
 *
 * 404 与别的失败分开说：服务端明确说没有这条信号，和「这次没读到」不是一件事（ADR-112）。
 */
async function loadHandedSignal() {
  handedSignal.value = null
  handedSignalGone.value = false
  handedAccountPicked.value = false
  const id = routeSignalId()
  if (id === null) return
  // 高级模式的「按信号 ID 执行」也落在同一条上：带过来的是哪条，就执行哪条。
  signalId.value = id
  try {
    handedSignal.value = await api.signal(id)
  } catch (e) {
    handedSignalGone.value = e instanceof ApiError && e.status === 404
  }
}

/** 带过来的信号属于这一版，而绑定这一版的账户恰好只有一个：那就选中它，并说明是替你选的。 */
async function openHandedAccount() {
  if (routeSignalId() === null || routeAccountId() !== null) return
  if (handedAccounts.value.length !== 1) return
  const only = handedAccounts.value[0]
  await openAccount(only.id, true)
  handedAccountPicked.value = true
}

/** 面板下拉里如果有这条信号，就选中它（没有就交给默认的「最新一条」，不硬塞）。 */
function selectHandedSignal() {
  const id = routeSignalId()
  if (id === null) return
  if (panelSignals.value.some((signal) => signal.id === id)) selectedSignalId.value = id
}

/**
 * 这一页读到地址里的信号之后要说的话：它是谁、会不会自动成交、账户替不替你选。
 *
 * 没有信号被带过来时返回空串（这一页照旧）。
 */
const handedNote = computed(() => {
  const id = routeSignalId()
  if (id === null) return ''
  const parts: string[] = []
  if (handedSignalGone.value) {
    parts.push(
      `从「信号」页带过来的信号 #${id} 在服务端已经没有了（可能已被删除），执行它会失败。`,
    )
  } else if (handedSignal.value) {
    const row = handedSignal.value
    parts.push(
      `从「信号」页带过来的信号 #${id}：${row.symbol ?? `#${row.asset_id}`} · ` +
        `${signalLabel(row.state)} · ${signalDirection(row.direction, row.closes_direction)} · ` +
        `${formatDateTime(row.bar_timestamp)}。`,
    )
  } else {
    parts.push(`地址里点名了信号 #${id}，但这次没读到它的内容。`)
  }
  parts.push('这一页不会自动下单：选好账户与仓位口径，按「执行」才成交。')

  if (!handedSignalGone.value) {
    const version = handedVersion.value
    if (handedAccountPicked.value && selectedAccount.value) {
      parts.push(
        `已替你选中唯一绑定策略版本 #${version} 的账户《${selectedAccount.value.name}》。`,
      )
    } else if (selectedAccount.value && selectedSignal.value?.id === id) {
      parts.push('已在上面的信号下拉里选中它。')
    } else if (selectedAccount.value) {
      const bound = selectedAccount.value.strategy_version_id ?? null
      parts.push(
        bound === null
          ? '这个账户没有绑定策略版本，下拉里只列最近 500 条信号，所以没有选中它：' +
              '换一个绑定了这一版的账户，或者在高级模式里直接按信号 ID 执行。'
          : `这个账户绑定的是策略版本 #${bound}，下拉里只列该版本的信号，所以没有选中它：` +
              `换一个绑定 #${version ?? '这一版'} 的账户，或者在高级模式里直接按信号 ID 执行。`,
      )
    } else if (accounts.value.length === 0) {
      parts.push(
        '还没有模拟账户：先在「新建模拟账户」里建一个' +
          (version === null ? '' : `（绑定策略版本 #${version} 就能在下拉里看到它）`) +
          '，再回来执行这条信号。',
      )
    } else if (handedAccounts.value.length > 1) {
      parts.push(
        `没有替你选账户：有 ${handedAccounts.value.length} 个账户都绑定策略版本 #${version}，` +
          '从上面的账户列表里挑一个。',
      )
    } else if (routeAccountId() !== null) {
      parts.push(
        `地址里点名的账户 #${routeAccountId()} 没有读出来（原因见上面的报错），所以这一屏还没有选中账户。`,
      )
    } else {
      parts.push(
        version === null
          ? '没有替你选账户：地址里没写这条信号属于哪一版，所以不知道哪个账户该执行它。'
          : `没有替你选账户：没有账户绑定策略版本 #${version}。`,
      )
    }
  }
  return parts.join('')
})

const performanceMissing = computed(() => {
  const value = performance.value
  if (!value) return false
  const total = value.metrics?.total_return
  return total === null || total === undefined
})

// 默认选中最新的一条信号；列表变了（换账户、执行完）就跟着重选，不让旧的选择悬空。
// 从信号页带过来的那一条只要在列表里就优先选中它 —— 地址里点名了哪条，就执行哪条（ADR-203）。
watch(panelSignals, (list) => {
  const handed = routeSignalId()
  if (handed !== null && list.some((signal) => signal.id === handed)) {
    selectedSignalId.value = handed
    return
  }
  if (!list.some((signal) => signal.id === selectedSignalId.value)) {
    selectedSignalId.value = list.length > 0 ? list[0].id : null
  }
})

// 数量与金额互斥：后填的一个清掉另一个，避免两个都发出去说不清按哪个成交。
watch(sizingQuantity, (value) => {
  if (positiveNumber(value) !== null) sizingNotional.value = null
})
watch(sizingNotional, (value) => {
  if (positiveNumber(value) !== null) sizingQuantity.value = null
})

watch(bindMode, (mode) => {
  if (mode === 'backtest') void loadBacktests()
  else if (mode === 'version') void loadReference()
})

// 「策略 → 版本」两级下拉：选了策略才去读它的版本，并默认选中当前版本。
watch(selectedStrategyId, async (strategyId) => {
  selectedVersionId.value = null
  versionOptions.value = []
  if (strategyId === null) return
  loadingVersions.value = true
  try {
    const rows = await api.strategyVersions(strategyId)
    versionOptions.value = rows
    const current = rows.find((v) => v.is_current) ?? rows[0]
    selectedVersionId.value = current ? current.id : null
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingVersions.value = false
  }
})

// URL 是选中账户的唯一来源：从回测页跳过来的 /paper?account=N、刷新、分享链接都走这条。
watch(
  () => routeAccountId(),
  (id) => {
    if (id !== null && id !== selectedAccountId.value) void openAccount(id, false)
  },
)

async function loadEquity(accountId: number) {
  error.value = ''
  loadingEquity.value = accountId
  try {
    const body = await api.paperEquity(accountId)
    equityCurve.value = body.equity_curve
    equityNote.value = body.curve_note
    equityAccount.value = body.account_id
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingEquity.value = null
  }
}

/**
 * 最近一批已持久化的信号：执行面板默认从这里挑最新的一条，不需要手抄 ID。
 *
 * 账户绑定了策略版本时，改问服务端要该版本的信号（`strategy_version_id`，ADR-181）。
 * 先取最近 500 条再在本页筛看似等价，实际会静默漏掉比这一批更旧的信号，所以不那样做。
 * 未绑定版本的账户仍然读最近 500 条，供「最新信号」卡与执行面板使用。
 */
async function loadSignals(version: number | null = scopedVersion.value) {
  loadingSignals.value = true
  try {
    if (version === null) {
      signalPool.value = await api.signals(undefined, 500)
      scopedSignals.value = []
      scopedVersion.value = null
    } else {
      scopedSignals.value = await api.signals(undefined, 500, undefined, 0, version)
      scopedVersion.value = version
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingSignals.value = false
  }
}

/**
 * 策略名与版本号：账户列表和回测列表都只给 id，界面要写「策略名 v1」就得先把这张
 * 对照表读出来。读不到不影响别的功能，标签会退化成 #id。
 */
async function loadReference() {
  if (referenceLoaded.value) return
  try {
    const [strategyRows, versionRows] = await Promise.all([
      api.strategies(),
      api.allStrategyVersions(),
    ])
    strategies.value = strategyRows
    allVersions.value = versionRows
    referenceLoaded.value = true
  } catch (e) {
    error.value = (e as Error).message
  }
}

/** 只列已经跑完的回测：还在跑的运行没有结果，绑上去对照不出任何东西。 */
async function loadBacktests() {
  if (backtestsLoaded.value || loadingBacktests.value) return
  loadingBacktests.value = true
  try {
    await loadReference()
    const rows = await api.backtests()
    backtests.value = rows.filter((run) => run.status === 'completed')
    backtestsLoaded.value = true
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingBacktests.value = false
  }
}

/**
 * 资产表只读一次做成 id → 符号的字典，让持仓表与流水表显示标的而不是 #3。
 * 读不到就退回编号：显示编号不算错，编一个符号才是错。
 */
async function loadAssetSymbols() {
  try {
    const rows = await api.assets()
    const map: Record<number, string> = {}
    for (const row of rows) map[row.id] = row.symbol
    assetSymbols.value = map
  } catch {
    // 只影响一列显示，不打扰用户，也不写进页面的错误条。
    assetSymbols.value = {}
  }
}

async function loadPositions(accountId: number) {
  error.value = ''
  try {
    positions.value = await api.paperPositions(accountId)
    activeAccount.value = accountId
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function loadTrades(accountId: number) {
  error.value = ''
  loadingTrades.value = accountId
  try {
    trades.value = (await api.paperTrades(accountId)) as unknown as PaperTradeRow[]
    tradesAccount.value = accountId
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingTrades.value = null
  }
}

/**
 * 选中一个账户：读它的持仓与流水，并把选择写回 URL 的 `?account=`，
 * 这样刷新页面和分享链接都会落在同一个账户上。
 *
 * 地址里原有的「带着哪条信号过来」（`?signal_id=`，ADR-203）不能在这一步被抹掉：
 * 换一个账户不该等于把要执行的那条信号忘掉。
 */
function paperQuery(accountId: number): Record<string, string> {
  const query: Record<string, string> = { account: String(accountId) }
  const signal = routeSignalId()
  if (signal !== null) query.signal_id = String(signal)
  const version = routeVersionId()
  if (version !== null) query.strategy_version_id = String(version)
  return query
}

async function openAccount(accountId: number, updateUrl: boolean) {
  if (accounts.value.length > 0 && !accounts.value.some((a) => a.id === accountId)) {
    error.value = `地址里的账户 #${accountId} 不在账户列表里（可能已被删除）：请从下面的列表重新选一个。`
    return
  }
  selectedAccountId.value = accountId
  if (updateUrl && routeAccountId() !== accountId) {
    await router.replace({ path: '/paper', query: paperQuery(accountId) })
  }
  await Promise.all([
    loadPositions(accountId),
    loadTrades(accountId),
    // 选中账户后才知道要哪个版本的信号（accounts 已经先加载过，见 bootstrap）。
    loadSignals(selectedAccount.value?.strategy_version_id ?? null),
  ])
}

async function selectAccount(account: PaperAccount) {
  await openAccount(account.id, true)
}

async function loadPerformance(accountId: number) {
  error.value = ''
  loadingPerf.value = accountId
  try {
    performance.value = (await api.paperPerformance(accountId)) as unknown as PaperPerformance
    perfAccount.value = accountId
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingPerf.value = null
  }
}

/** Why the return metrics are missing, in the backend's own words when it says so. */
function performanceNotice(value: PaperPerformance): string {
  const notes = Array.isArray(value.metric_notes) ? value.metric_notes.join('；') : ''
  if (notes) return `没有发布收益率类指标：${notes}`
  return '净入金为 0 或为负（提现已经取走了全部本金），此时收益率没有分母，因此不发布收益率类指标。'
}

async function load() {
  error.value = ''
  try {
    accounts.value = await api.paperAccounts()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function create() {
  error.value = ''
  info.value = ''
  if (name.value.trim().length === 0) {
    error.value = '账户名称不能为空'
    return
  }
  const initial = positiveNumber(cash.value)
  if (initial === null) {
    error.value = '初始资金必须大于 0'
    return
  }
  const binding: { strategyVersionId?: number; backtestRunId?: number } = {}
  if (bindMode.value === 'backtest') {
    if (selectedBacktestId.value === null) {
      error.value = '请选择一次已完成的回测，或者把绑定方式改成「不绑定」。'
      return
    }
    binding.backtestRunId = selectedBacktestId.value
  } else if (bindMode.value === 'version') {
    if (selectedVersionId.value === null) {
      error.value = '请选择策略版本，或者把绑定方式改成「不绑定」。'
      return
    }
    binding.strategyVersionId = selectedVersionId.value
  }

  creating.value = true
  try {
    const created = await api.createPaperAccount(name.value.trim(), initial, binding)
    info.value = `已创建 ${created.name}（#${created.id}，${bindingLabel(created)}），初始资金 ${formatNumber(initial)}`
    await load()
    await openAccount(created.id, true)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    creating.value = false
  }
}

/** 二选一的仓位口径：填了数量就用数量，填了金额就用金额，都不填＝引擎默认整仓。 */
function sizingPayload(): { quantity?: number; notional?: number } {
  const quantity = positiveNumber(sizingQuantity.value)
  if (quantity !== null) return { quantity }
  const notional = positiveNumber(sizingNotional.value)
  if (notional !== null) return { notional }
  return {}
}

/** 成交之后要重读的东西：账户、持仓、流水，以及正好在看的绩效与曲线。 */
async function refreshAfterExecution(accountId: number) {
  await load()
  await Promise.all([loadPositions(accountId), loadTrades(accountId)])
  if (equityAccount.value === accountId) await loadEquity(accountId)
  if (perfAccount.value === accountId) await loadPerformance(accountId)
}

async function executeSignal(account: PaperAccount) {
  error.value = ''
  info.value = ''
  const id = positiveNumber(signalId.value)
  if (id === null) {
    error.value = '请填写信号 ID'
    return
  }
  busy.value = account.id
  try {
    const r = await api.executePaperSignal(account.id, id, sizingPayload())
    lastFill.value = r
    fillAccount.value = account.id
    info.value = `${account.name} 已执行信号 #${id}`
    await refreshAfterExecution(account.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
  }
}

/** 执行下拉里选中的那条信号（普通模式的主要入口）。 */
async function executeSelectedSignal() {
  const account = selectedAccount.value
  if (account === null) {
    error.value = '先在「账户明细」里选中一个账户，再执行信号。'
    return
  }
  const signal = selectedSignal.value
  if (signal === null) {
    error.value = '没有可执行的信号：先到「信号」页扫描并持久化一条信号。'
    return
  }
  signalId.value = signal.id
  await executeSignal(account)
  await loadSignals()
}

async function executeFromPanel(account: PaperAccount, id: number) {
  signalId.value = id
  await executeSignal(account)
  await loadSignals()
}

/**
 * Closing an account is a dangerous button, so it asks first and says what changes
 * (review report P2-14): new fills stop, the recorded trades stay, and it can be
 * reopened. `setStatus` keeps doing the work.
 */
async function closeAccount(account: PaperAccount) {
  const ok = window.confirm(
    `关闭「${account.name}」之后这个账户不再接受新的虚拟成交，已经记下的持仓与交易记录都会保留。随时可以「重开」。确定关闭？`,
  )
  if (!ok) return
  await setStatus(account, 'close')
}

async function setStatus(account: PaperAccount, action: 'close' | 'reopen') {
  error.value = ''
  info.value = ''
  busy.value = account.id
  try {
    const r =
      action === 'close'
        ? await api.closePaperAccount(account.id)
        : await api.reopenPaperAccount(account.id)
    info.value = `${account.name} 状态：${accountStatusLabel(r.status)}`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
  }
}

async function resetAccount(account: PaperAccount) {
  const ok = window.confirm(
    `重置「${account.name}」会删除全部虚拟持仓与交易记录，并把资金基准改回 ${formatNumber(account.net_deposits)}。此操作不可撤销，确定继续？`,
  )
  if (!ok) return
  error.value = ''
  info.value = ''
  busy.value = account.id
  try {
    const r = await api.resetPaperAccount(account.id)
    info.value = `${account.name} 已重置：现金 ${formatNumber(r.cash)}，第 ${r.reset_count} 次重置`
    await refreshAfterExecution(account.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
  }
}

async function fund(account: PaperAccount) {
  error.value = ''
  info.value = ''
  busy.value = account.id
  try {
    const r = await api.fundPaperAccount(account.id, fundAmount.value)
    info.value = `${account.name} 虚拟资金调整后现金：${formatNumber(r.cash)}（入金/提现只移动资金基准，不算收益、也不算回撤）`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
  }
}

/**
 * 回测 vs 模拟（评审 §15、ADR-130）：模拟盘是验证工具，所以它最有用的一屏是「和回测比」。
 *
 * 两个事实必须同时说：① 两列读数都来自已经存下来的东西（回测用一次已完成回测的读数，
 * 模拟用绩效端点的读数），这一页不重新计算任何指标；② 两边的时间跨度不一样长，所以这两个
 * 收益不能直接比大小 —— 时间跨度只由曲线首尾时间戳算出差多少天，用来解释这件事，而不是
 * 拿它去折算或修正收益。
 *
 * 对照谁：优先账户自己记下的那次回测（ADR-181，那才是让它开出来的那次运行），读不到才
 * 退回「绑定版本最近一次跑完的回测」。
 */
type ComparisonSide = {
  total_return: number | null
  max_drawdown: number | null
  trades: number | null
  window: string
  detail: string
}

type Comparison = {
  accountId: number
  accountName: string
  backtest: ComparisonSide | null
  paper: ComparisonSide | null
  note: string
}

const comparison = ref<Comparison | null>(null)
const comparing = ref<number | null>(null)

/** 曲线首尾差多少天：只用来描述两边跑了多久，不是量化指标。 */
function spanLabel(timestamps: Array<string | undefined>): string {
  const usable = timestamps.filter((t): t is string => typeof t === 'string' && t.length > 0)
  if (usable.length < 2) return ''
  const first = new Date(usable[0]).getTime()
  const last = new Date(usable[usable.length - 1]).getTime()
  if (!Number.isFinite(first) || !Number.isFinite(last) || last <= first) return ''
  const days = Math.round((last - first) / 86400000)
  if (days < 60) return `约 ${days} 天`
  if (days < 730) return `约 ${Math.round(days / 30)} 个月`
  return `约 ${(days / 365).toFixed(1)} 年`
}

async function loadComparison(account: PaperAccount) {
  error.value = ''
  comparing.value = account.id
  comparison.value = null
  try {
    const result: Comparison = {
      accountId: account.id,
      accountName: account.name,
      backtest: null,
      paper: null,
      note: '',
    }
    const versionId = account.strategy_version_id ?? null
    const strategyId = account.strategy_id ?? (versionId === null ? null : versionOwner(versionId))

    if (strategyId === null && versionId === null && account.backtest_run_id == null) {
      result.note =
        '这个账户没有绑定策略，所以没有可以对照的历史回测。绑定一个策略之后，这里会把两边并排放在一起。'
      comparison.value = result
      return
    }

    let note = ''
    let run: BacktestSummary | null = null
    let runDetail: BacktestDetail | null = null
    let runLabel = ''

    if (account.backtest_run_id !== null && account.backtest_run_id !== undefined) {
      try {
        const detail = await api.backtest(account.backtest_run_id)
        if (detail.status === 'completed') {
          runDetail = detail
          run = detail
          runLabel = `账户绑定的第 #${detail.id} 次回测`
        } else {
          note = `账户绑定的第 #${account.backtest_run_id} 次回测还没有跑完（${detail.status}），等它跑完这里才有对照。`
        }
      } catch (e) {
        note = `账户记下的第 #${account.backtest_run_id} 次回测读不到了（可能已被删除）：${(e as Error).message}`
      }
    }

    if (run === null) {
      const candidates: number[] = []
      if (versionId !== null) {
        candidates.push(versionId)
      } else if (strategyId !== null) {
        const versions = await api.strategyVersions(strategyId)
        const version = versions.find((v) => v.is_current) ?? versions[0]
        if (version) candidates.push(version.id)
      }
      for (const candidate of candidates) {
        const runs = await api.backtests(candidate)
        const completed = runs.find((r) => r.status === 'completed')
        if (completed) {
          run = completed
          runLabel = `${strategyName(strategyId)} ${versionLabel(candidate)} 的第 #${completed.id} 次回测`
          break
        }
      }
    }

    if (run !== null) {
      const detail = runDetail ?? (await api.backtest(run.id))
      result.backtest = {
        total_return: run.total_return,
        max_drawdown: run.max_drawdown,
        trades: run.number_of_trades,
        window: spanLabel(detail.equity_curve.map((p) => p.timestamp)),
        detail: runLabel,
      }
    } else if (note === '') {
      note = '这个策略版本还没有跑完的回测，先在「回测」页跑一次，这里才有对照。'
    }

    const perf = await api.paperPerformance(account.id)
    const equity = await api.paperEquity(account.id)
    result.paper = {
      total_return: perf.metrics?.total_return ?? null,
      max_drawdown: perf.metrics?.max_drawdown ?? null,
      trades: perf.closed_trades ?? null,
      window: spanLabel(equity.equity_curve.map((p) => p.timestamp)),
      detail: `模拟账户 #${account.id} 的 ${equity.trades_count} 笔成交记录`,
    }
    result.note = note
    comparison.value = result
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    comparing.value = null
  }
}

onMounted(() => {
  void bootstrap()
})

/**
 * 先把账户读出来，再按 URL 选中（顺序很重要：选中的账户必须先在列表里）。
 *
 * URL 里可能同时点着两件东西：要执行的信号（`?signal_id=`）与要看的账户（`?account=`）。
 * 账户写在地址里就以它为准；没写、而这条信号只对应一个账户时，才替用户选中那一个 ——
 * 有多个候选时谁也不比谁更该被选中，宁可不选（ADR-203）。
 */
async function bootstrap() {
  await load()
  await loadSignals()
  void loadReference()
  void loadAssetSymbols()
  await loadHandedSignal()
  const fromUrl = routeAccountId()
  if (fromUrl !== null) await openAccount(fromUrl, false)
  else await openHandedAccount()
  selectHandedSignal()
}
</script>

<template>
  <div>
    <h1 class="page-title">模拟验证</h1>
    <p class="page-sub">
      这一页不是记账工具，而是策略的验证工具：让同一个策略在一段真实走过的时间里用虚拟资金成交，
      看它和回测差多少。模拟账户使用虚拟资金，与 Ghostfolio 真实持仓完全隔离。成交按「信号参考价 +
      滑点」计算并计入手续费；V1 为多头单持仓，且不包含任何券商下单接口。
      账户可以绑定一个策略版本或一次已完成的回测，这样信号能从下拉里选、绩效也能和那次回测对照。
      算不出来的数字一律写「未知」并给出原因：0 和「不知道」是两件事。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>
    <p v-if="handedNote" class="notice">{{ handedNote }}</p>

    <div class="card card-quiet">
      <h3>新建模拟账户</h3>
      <div class="row">
        <input v-model="name" style="max-width: 220px" placeholder="账户名称" />
        <input
          v-model.number="cash"
          type="number"
          min="0"
          style="max-width: 180px"
          placeholder="初始资金（必须 > 0）"
        />
        <select v-model="bindMode" style="max-width: 200px">
          <option value="none">不绑定</option>
          <option value="backtest">从回测记录创建</option>
          <option value="version">绑定策略版本</option>
        </select>
        <button :disabled="creating || !canCreate" @click="create">创建</button>
      </div>
      <p v-if="!canCreate" class="muted" style="margin-top: 8px">
        金额和名称是必填的：需要一个账户名称，以及大于 0 的初始资金。初始资金是这一套虚拟资金的基准，
        之后入金/提现都只移动这个基准，不算收益也不算回撤（ADR-066）。
      </p>

      <template v-if="bindMode === 'backtest'">
        <p class="muted" style="margin-top: 10px">
          只列出已经跑完（status = completed）的回测。选中之后，账户会绑定那次回测的策略版本，
          以及它是用哪条标的、哪个周期跑的。
        </p>
        <div class="row" style="margin-top: 8px">
          <select
            v-model="selectedBacktestId"
            style="max-width: 640px"
            :disabled="loadingBacktests"
          >
            <option :value="null">— 选择一次已完成的回测 —</option>
            <option v-for="run in backtests" :key="run.id" :value="run.id">
              {{ backtestLabel(run) }}
            </option>
          </select>
        </div>
        <p v-if="loadingBacktests" class="muted" style="margin-top: 6px">读取回测记录…</p>
        <p v-else-if="!backtests.length" class="muted" style="margin-top: 6px">
          还没有跑完的回测。先到「回测」页跑一次，再回来用「从回测记录创建」。
        </p>
        <p v-if="pendingBinding" class="notice" style="margin-top: 8px">{{ pendingBinding }}</p>
      </template>

      <template v-if="bindMode === 'version'">
        <p class="muted" style="margin-top: 10px">
          先选策略，再选版本：模拟盘的结果只有记到具体版本上，才能和那个版本的回测对上。
        </p>
        <div class="row" style="margin-top: 8px">
          <select v-model="selectedStrategyId" style="max-width: 300px">
            <option :value="null">— 选择策略 —</option>
            <option v-for="s in strategies" :key="s.id" :value="s.id">
              {{ s.name }}（{{ s.version_count }} 个版本）
            </option>
          </select>
          <select
            v-model="selectedVersionId"
            style="max-width: 240px"
            :disabled="loadingVersions || selectedStrategyId === null"
          >
            <option :value="null">— 选择版本 —</option>
            <option v-for="v in versionOptions" :key="v.id" :value="v.id">
              v{{ v.version }}{{ v.is_current ? '（当前版本）' : '' }}
            </option>
          </select>
        </div>
        <p v-if="loadingVersions" class="muted" style="margin-top: 6px">读取策略版本…</p>
        <p v-if="pendingBinding" class="notice" style="margin-top: 8px">{{ pendingBinding }}</p>
      </template>
    </div>

    <div v-if="accounts.length" class="grid cols-3" style="margin-top: 14px">
      <StatCard
        v-for="a in accounts"
        :key="a.id"
        :label="a.name"
        :value="formatNumber(a.cash)"
        :tone="toneOf(a.realized_pnl)"
        :sub="`净入金 ${formatNumber(a.net_deposits)} ${a.base_currency} · 已实现盈亏 ${formatNumber(a.realized_pnl)}（${formatPaperPnlPct(a.net_deposits, a.realized_pnl)}）`"
      />
    </div>

    <div v-if="accounts.length" class="card" style="margin-top: 14px">
      <h3>账户明细</h3>
      <p class="muted">
        点某一行的「选中」之后，下面会出现这个账户的总览、持仓与流水，执行信号也会以它为准；
        回测页跳过来的链接（/paper?account=…）会自动选中对应的账户。
      </p>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>绑定</th>
            <th>净入金</th>
            <th>当前现金</th>
            <th>已实现盈亏</th>
            <th>状态</th>
            <th>重置次数</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="a in accounts"
            :key="a.id"
            :class="{ 'account-selected': a.id === selectedAccountId }"
          >
            <td>{{ a.id }}</td>
            <td>{{ a.name }}</td>
            <td>{{ bindingLabel(a) }}</td>
            <td>{{ formatNumber(a.net_deposits) }}</td>
            <td>{{ formatNumber(a.cash) }}</td>
            <td :class="toneOf(a.realized_pnl)">{{ formatNumber(a.realized_pnl) }}</td>
            <td>{{ accountStatusLabel(a.status) }}</td>
            <td>{{ a.reset_count }}</td>
            <td>{{ formatDateTime(a.created_at) }}</td>
            <td>
              <button
                class="ghost"
                :aria-pressed="a.id === selectedAccountId"
                @click="selectAccount(a)"
              >
                {{ a.id === selectedAccountId ? '已选中' : '选中' }}
              </button>
              <button class="ghost" @click="loadPositions(a.id)">持仓</button>
              <button class="ghost" :disabled="loadingPerf === a.id" @click="loadPerformance(a.id)">
                {{ loadingPerf === a.id ? '计算中…' : '绩效' }}
              </button>
              <button class="ghost" :disabled="loadingEquity === a.id" @click="loadEquity(a.id)">
                {{ loadingEquity === a.id ? '读取中…' : '权益曲线' }}
              </button>
              <button class="ghost" :disabled="comparing === a.id" @click="loadComparison(a)">
                {{ comparing === a.id ? '对比中…' : '回测 vs 模拟' }}
              </button>
              <button
                v-if="a.status === 'active'"
                class="ghost danger"
                :disabled="busy === a.id"
                @click="closeAccount(a)"
              >
                关闭
              </button>
              <button
                v-else
                class="ghost"
                :disabled="busy === a.id"
                @click="setStatus(a, 'reopen')"
              >
                重开
              </button>
              <button class="danger" :disabled="busy === a.id" @click="resetAccount(a)">
                重置
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="notice warn" style="margin-top: 12px">
        重置模拟账户会清空全部虚拟持仓与交易记录，并写入审计日志。真实账户不受任何影响。
      </p>
    </div>

    <!-- 账户总览：钱从哪来、现在值多少、哪些数字没有算出来。恒等式写在下面让用户自己核对。 -->
    <div v-if="selectedAccount" class="card" style="margin-top: 14px">
      <h3>账户总览（{{ selectedAccount.name }} #{{ selectedAccount.id }}）</h3>
      <p class="muted">
        {{ accountStatusLabel(selectedAccount.status) }} · {{ selectedAccount.base_currency }} ·
        创建于 {{ formatDateTime(selectedAccount.created_at) }} · 已重置 {{ selectedAccount.reset_count }} 次 ·
        绑定：{{ bindingLabel(selectedAccount) }}
      </p>
      <table style="margin-top: 10px">
        <thead>
          <tr>
            <th>这一项</th>
            <th>数值</th>
            <th>怎么来的</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>净入金</td>
            <td>{{ formatNumber(selectedAccount.net_deposits) }}</td>
            <td class="muted">入金 − 提现。所有收益率的基准，提现只是把基准挪小</td>
          </tr>
          <tr>
            <td>现金</td>
            <td>{{ formatNumber(selectedAccount.cash) }}</td>
            <td class="muted">还没动用的虚拟现金</td>
          </tr>
          <tr>
            <td>持仓市值</td>
            <td>{{ moneyOrUnknown(selectedAccount.market_value) }}</td>
            <td class="muted">
              {{
                selectedAccount.market_value == null
                  ? '未知：这次没有可用来标记持仓的最新已收盘 K 线'
                  : '现金之外的持仓，按最新已收盘 K 线标记'
              }}
            </td>
          </tr>
          <tr>
            <td>未实现盈亏</td>
            <td :class="toneOf(selectedAccount.unrealized_pnl)">
              {{ moneyOrUnknown(selectedAccount.unrealized_pnl) }}
            </td>
            <td class="muted">
              {{
                selectedAccount.unrealized_pnl == null
                  ? '未知：没有标记价就算不出浮动盈亏（不是 0）'
                  : '持仓还没平仓的浮动盈亏'
              }}
            </td>
          </tr>
          <tr>
            <td>已实现盈亏</td>
            <td :class="toneOf(selectedAccount.realized_pnl)">
              {{ formatNumber(selectedAccount.realized_pnl) }}
            </td>
            <td class="muted">只统计已平仓的交易，不从现金倒推（ADR-124）</td>
          </tr>
          <tr>
            <td>总资产</td>
            <td :class="toneOf(selectedAccount.total_pnl)">
              {{ moneyOrUnknown(selectedAccount.total_equity) }}
            </td>
            <td class="muted">
              {{
                selectedAccount.total_equity == null
                  ? '未知：现金 + 持仓市值里有一项是未知'
                  : '现金 + 持仓市值'
              }}
            </td>
          </tr>
          <tr>
            <td>总收益率</td>
            <td :class="toneOf(selectedAccount.total_pnl_pct)">
              {{ pctOrUnknown(selectedAccount.total_pnl_pct) }}
            </td>
            <td class="muted">
              {{
                selectedAccount.total_pnl_pct == null
                  ? '未知：净入金 ≤ 0 时收益率没有分母（ADR-066）'
                  : '总盈亏 ÷ 净入金；入金/提现只移动基准'
              }}
            </td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="margin-top: 10px">
        总资产 = 现金 + 持仓市值：
        <span class="mono">{{ moneyOrUnknown(selectedAccount.total_equity) }}</span> =
        <span class="mono">{{ formatNumber(selectedAccount.cash) }}</span> +
        <span class="mono">{{ moneyOrUnknown(selectedAccount.market_value) }}</span>
        <span v-if="equityIdentityMismatch" class="neg">
          （服务端给的总资产与这条恒等式对不上，请以服务端为准并核对这笔数据）
        </span>
      </p>
      <p v-if="summaryNotes" class="muted" style="margin-top: 6px">
        服务端对缺失指标的说明：{{ summaryNotes }}
      </p>
      <p class="muted" style="margin-top: 6px">
        入金和提现不是盈利也不是亏损：它们只把「净入金」这个基准往前或往后挪（ADR-066）。
        未实现盈亏来自持仓表里每一行的标记价，没有标记价时宁可写「未知」也不写 0（ADR-007）。
      </p>
    </div>

    <!-- 回测 vs 模拟（评审 §15、ADR-130）：这一屏回答「模拟盘有没有严重偏离回测」，
         并且明说两列的时间跨度不同、暂时不能直接比大小。 -->
    <div v-if="comparison" class="card comparison-card" style="margin-top: 14px">
      <h3>回测 vs 模拟（{{ comparison.accountName }}）</h3>
      <template v-if="comparison.backtest && comparison.paper">
        <p class="muted">
          同一套策略信号，左边是它在整段历史数据上的回测，右边是它在模拟账户里真实走过的这一段。
          <b>两边的收益不能直接比大小</b>：时间跨度不一样长，回测跑了很多年，模拟盘才刚开始。
          这一屏看的是「模拟盘有没有严重偏离回测」，不是给模拟盘打分。
        </p>
        <table>
          <thead>
            <tr>
              <th>读数</th>
              <th>
                历史回测<span v-if="comparison.backtest.window">
                  （{{ comparison.backtest.window }}）</span
                >
              </th>
              <th>
                模拟验证<span v-if="comparison.paper.window">（{{ comparison.paper.window }}）</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>累计收益</td>
              <td :class="toneOf(comparison.backtest.total_return)">
                {{ formatPercent(comparison.backtest.total_return) }}
              </td>
              <td :class="toneOf(comparison.paper.total_return)">
                {{ formatPercent(comparison.paper.total_return) }}
              </td>
            </tr>
            <tr>
              <td>最大回撤</td>
              <td :class="toneOf(comparison.backtest.max_drawdown)">
                {{ formatPercent(comparison.backtest.max_drawdown) }}
              </td>
              <td :class="toneOf(comparison.paper.max_drawdown)">
                {{ formatPercent(comparison.paper.max_drawdown) }}
              </td>
            </tr>
            <tr>
              <td>交易次数</td>
              <td>{{ comparison.backtest.trades ?? '—' }}</td>
              <td>{{ comparison.paper.trades ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted">
          回测那一列来自 {{ comparison.backtest.detail }}；模拟那一列来自
          {{ comparison.paper.detail }}。两边的收益率都只统计已平仓的部分。
        </p>
        <p class="notice">
          模拟盘目前运行的时间比历史回测短得多，暂时不能与多年历史回测直接比较：样本还没攒够，
          收益和回撤都还只是开头。等模拟盘跑够时间再回来看这一屏。
        </p>
      </template>
      <p v-else class="muted">{{ comparison.note }}</p>
    </div>

    <div v-if="accounts.length" class="card card-quiet" style="margin-top: 14px">
      <h3>执行信号（虚拟成交）</h3>
      <p class="muted">
        信号不会自动成交：执行哪一条由你决定，位置由你决定。选一个账户、一条信号、一种仓位口径，
        再点「执行」。BUY 开仓、SELL 平仓；账户已关闭时拒绝成交。
      </p>

      <p v-if="!selectedAccount" class="muted" style="margin-top: 8px">
        还没有选中账户：先在上面「账户明细」里点某一行的「选中」。选中之后，这一屏才知道用哪个账户，
        以及该按哪个策略版本的信号筛。
      </p>

      <template v-else>
        <p class="muted" style="margin-top: 8px">
          账户：<b>{{ selectedAccount.name }}</b>（#{{ selectedAccount.id }} ·
          {{ accountStatusLabel(selectedAccount.status) }} · 现金
          {{ formatNumber(selectedAccount.cash) }}）。<template
            v-if="selectedAccount.strategy_version_id != null"
          >
            只列出绑定策略版本 #{{ selectedAccount.strategy_version_id }} 的信号。
          </template>
          <template v-else>
            该账户未绑定策略版本，显示全部信号。
          </template>
          按版本的筛选由服务端完成（ADR-181），所以更旧的信号也不会漏掉。
        </p>

        <div class="row" style="margin-top: 8px">
          <select v-model="selectedSignalId" style="max-width: 620px">
            <option :value="null">— 选择信号 —</option>
            <option v-for="s in panelSignals" :key="s.id" :value="s.id">
              {{ signalOptionLabel(s) }}
            </option>
          </select>
        </div>
        <p v-if="loadingSignals" class="muted" style="margin-top: 6px">读取中…</p>
        <p v-else-if="!panelSignals.length" class="muted" style="margin-top: 6px">
          {{
            selectedAccount.strategy_version_id != null
              ? '这个策略版本还没有已持久化的信号：到「信号」页跑一次扫描并持久化，或等定时任务产生信号。'
              : '还没有持久化信号。到「信号」页跑一次扫描并持久化，或等定时任务产生信号。'
          }}
        </p>
        <p v-else-if="selectedSignal" class="muted" style="margin-top: 6px">
          选中信号 #{{ selectedSignal.id }}：{{ signalLabel(selectedSignal.state) }} ·
          {{ signalDirection(selectedSignal.direction, selectedSignal.closes_direction) }} ·
          {{
            selectedSignal.price_reference == null
              ? '参考价未知（这条信号没有参考价）'
              : `参考价 ${formatNumber(selectedSignal.price_reference)}`
          }}
          · {{ formatDateTime(selectedSignal.bar_timestamp) }}（{{ timeframeLabel(selectedSignal.timeframe) }}）
        </p>

        <div class="row" style="margin-top: 8px">
          <input
            v-model.number="sizingQuantity"
            type="number"
            min="0"
            style="max-width: 170px"
            placeholder="数量（单位）"
          />
          <input
            v-model.number="sizingNotional"
            type="number"
            min="0"
            style="max-width: 170px"
            :placeholder="`金额（${selectedAccount.base_currency}）`"
          />
          <button
            :disabled="selectedAccount === null || busy === selectedAccount.id || !selectedSignal"
            @click="executeSelectedSignal"
          >
            执行 → {{ selectedAccount.name }}
          </button>
        </div>
        <p class="muted" style="margin-top: 6px">
          数量和金额二选一：填了数量就按数量成交，填了金额就按金额成交（互斥，后填的会清掉另一个）。
          两个都不填＝按引擎默认的整仓下单（受 max_position_pct 限制，默认用 100% 可用资金），
          这是最省事、也是最接近回测里那种「整仓进出」的口径。
        </p>

        <p v-if="lastFill && fillAccount === selectedAccount.id" class="notice" style="margin-top: 8px">
          上次成交（订单 #{{ lastFill.order_id }}）：{{ lastFill.side }} · 成交价
          {{ formatNumber(lastFill.fill_price) }} · 数量 {{ formatNumber(lastFill.quantity) }} · 费用
          {{ formatNumber(lastFill.fees) }} · 滑点 {{ formatNumber(lastFill.slippage) }} · 已实现盈亏
          {{ formatNumber(lastFill.realized_pnl) }} · 成交后现金 {{ formatNumber(lastFill.cash) }}
        </p>
      </template>

      <template v-if="isAdvanced">
        <p class="muted" style="margin-top: 10px">
          高级模式：也可以直接按信号 ID 执行（ID 见「信号」页的持久化记录），或者对某个账户调仓。
          上面的「数量 / 金额」同样适用，都不填就是引擎默认整仓。
        </p>
        <div class="row" style="margin-top: 8px">
          <input
            v-model.number="signalId"
            type="number"
            min="1"
            style="max-width: 140px"
            placeholder="信号 ID"
          />
          <button
            v-for="a in accounts"
            :key="a.id"
            :disabled="busy === a.id || !signalId"
            @click="executeSignal(a)"
          >
            执行 → {{ a.name }}
          </button>
        </div>
        <p v-if="!signalId" class="muted" style="margin-top: 8px">
          按钮现在点不动，因为还没有填写信号 ID：填一个已持久化的信号 ID，再选账户执行。
        </p>
      </template>

      <div class="row" style="margin-top: 8px">
        <input v-model.number="fundAmount" type="number" style="max-width: 160px" placeholder="注资金额（可为负）" />
        <button
          v-for="a in accounts"
          :key="`fund-${a.id}`"
          class="ghost"
          :disabled="busy === a.id"
          @click="fund(a)"
        >
          注资/出金 → {{ a.name }}
        </button>
      </div>
      <p class="muted" style="margin-top: 6px">
        注资和出金只改变虚拟现金与「净入金」基准，不会记成收益或亏损（ADR-066）。
      </p>
    </div>

    <div v-if="activeAccount !== null" class="card" style="margin-top: 14px">
      <h3>持仓（账户 #{{ activeAccount }}）</h3>
      <p class="muted">
        最新收盘价 = 该标的最近一根「已收盘」K 线的收盘价，市值与未实现盈亏都由它标记。
        没有合格的收盘价时这一行写「未知」并给出原因，而不是编一个价格（ADR-007/ADR-023）。
      </p>
      <table v-if="positions.length">
        <thead>
          <tr>
            <th>资产</th>
            <th>数量</th>
            <th>成本价</th>
            <th>最新收盘价</th>
            <th>市值</th>
            <th>未实现盈亏</th>
            <th>已实现盈亏</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in positions" :key="p.id">
            <td>{{ assetLabel(p.asset_id, p.symbol) }}</td>
            <td>{{ formatNumber(p.quantity) }}</td>
            <td>{{ formatNumber(p.avg_cost) }}</td>
            <td>
              <template v-if="p.mark_price == null">
                <span class="muted">未知</span>
                <div class="muted">{{ markReason(p) }}</div>
              </template>
              <template v-else>
                {{ formatNumber(p.mark_price) }}
                <div class="muted">{{ formatDateTime(p.mark_time) }}</div>
              </template>
            </td>
            <td>
              {{ moneyOrUnknown(p.market_value) }}
              <div v-if="p.market_value == null" class="muted">{{ markReason(p) }}</div>
            </td>
            <td :class="toneOf(p.unrealized_pnl)">
              {{ moneyOrUnknown(p.unrealized_pnl) }}
              <div class="muted">
                {{ p.unrealized_pnl == null ? markReason(p) : pctOrUnknown(p.unrealized_pnl_pct) }}
              </div>
            </td>
            <td :class="toneOf(p.realized_pnl)">{{ formatNumber(p.realized_pnl) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">该账户当前没有持仓。</p>
    </div>

    <div v-if="tradesAccount !== null" class="card" style="margin-top: 14px">
      <h3>交易流水（账户 #{{ tradesAccount }}）</h3>
      <p class="muted">
        逐笔成交记录，来自后端的逐笔流水接口。R 倍数 = 这笔交易的盈亏 ÷ 它承担的风险
        （入场价与止损的距离）：1R 表示赚到的钱正好等于当初愿意亏的钱。还没平仓的那一笔没有
        盈亏。费用与滑点是这一笔真实付出的成本，「来源」写的是产生这笔成交的信号编号。
        读不到的量写「未知」，不写 0。
      </p>
      <p v-if="loadingTrades === tradesAccount" class="muted">读取中…</p>
      <table v-else-if="trades.length">
        <thead>
          <tr>
            <th>ID</th>
            <th>方向</th>
            <th>数量</th>
            <th>入场时间</th>
            <th>入场价</th>
            <th>出场时间</th>
            <th>出场价</th>
            <th>盈亏</th>
            <th>R 倍数</th>
            <th>费用 / 滑点</th>
            <th>平仓原因</th>
            <th>策略版本</th>
            <th>来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in trades" :key="t.id">
            <td>{{ t.id }}</td>
            <td>{{ signalDirection(t.direction) }}</td>
            <td>{{ formatNumber(t.quantity) }}</td>
            <td>{{ formatDateTime(t.entry_time) }}</td>
            <td>{{ formatNumber(t.entry_price) }}</td>
            <td>{{ t.exit_time ? formatDateTime(t.exit_time) : '未平仓' }}</td>
            <td>{{ t.exit_price == null ? '—' : formatNumber(t.exit_price) }}</td>
            <td :class="toneOf(t.pnl)">
              {{ t.pnl == null ? '未知（还没平仓）' : formatNumber(t.pnl) }}
            </td>
            <td>{{ t.r_multiple == null ? '未知' : formatNumber(t.r_multiple) }}</td>
            <td>{{ moneyOrUnknown(t.fees) }} / {{ moneyOrUnknown(t.slippage) }}</td>
            <td>{{ t.reason || '—' }}</td>
            <td>{{ t.strategy_version || '—' }}</td>
            <td>
              <RouterLink v-if="t.signal_id != null" :to="`/signals?signal_id=${t.signal_id}`">
                信号 #{{ t.signal_id }}
              </RouterLink>
              <span v-else class="muted">没有来源</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">这个账户还没有成交记录。执行一条信号之后，这里会出现逐笔明细。</p>
      <p v-if="trades.length" class="muted" style="margin-top: 8px">
        「来源」一列是从逐笔流水本身读出来的：成交单记着它当时为哪条信号下的，一笔成交记着
        自己的成交单，所以这里写的是那一笔自己的来历，不是把账户里所有成交都倒推给某一版策略
        （归因仍然按账户，ADR-114）。点「信号 #N」会打开那条信号的详情卡，看它当时为什么发出。
        「没有来源」表示这笔成交不是从信号来的。费用是这一笔真实付出的手续费（平仓之后包含
        进场与出场两次），滑点是成交价与当时基准价的差；R 倍数对模拟盘一律写「未知」，因为纸面
        引擎不设止损、也就没有「当初愿意亏多少」这个基准，这一页不会编一个数出来。
      </p>
    </div>

    <div v-if="performance" class="card" style="margin-top: 14px">
      <h3>绩效（账户 #{{ perfAccount }}）</h3>
      <p class="muted">{{ performance.note }}</p>
      <div class="grid cols-4">
        <StatCard
          label="期末权益"
          :value="moneyOrUnknown(performance.final_equity)"
          sub="净入金 + 已实现"
        />
        <StatCard
          label="总收益"
          :value="pctOrUnknown(performance.metrics?.total_return)"
          :tone="toneOf(performance.metrics?.total_return)"
          sub="已平仓"
        />
        <StatCard
          label="最大回撤"
          :value="pctOrUnknown(performance.metrics?.max_drawdown)"
          :tone="toneOf(performance.metrics?.max_drawdown)"
          sub="越小越好"
        />
        <StatCard
          label="胜率"
          :value="pctOrUnknown(performance.metrics?.win_rate)"
          :sub="`交易 ${performance.closed_trades} 次`"
        />
      </div>
      <p v-if="performanceMissing" class="muted" style="margin-top: 10px">
        {{ performanceNotice(performance) }}
      </p>
    </div>

    <div v-if="equityCurve.length" class="card" style="margin-top: 14px">
      <h3>权益曲线（账户 #{{ equityAccount }}）</h3>
      <EquityChart :points="equityCurve" />
      <p class="muted" style="margin-top: 10px">{{ equityNote }}</p>
    </div>

    <div v-if="accounts.length" class="card" style="margin-top: 14px">
      <h3>最新信号</h3>
      <p class="muted">
        最近 10 条已持久化的信号。要执行就点那一行右侧的「→ 账户名」，不必手抄信号 ID；
        手抄 ID 的入口留在高级模式。
      </p>
      <p v-if="loadingSignals" class="muted">读取中…</p>
      <table v-else-if="recentSignals.length">
        <thead>
          <tr>
            <th>ID</th>
            <th>生成时间</th>
            <th>资产</th>
            <th>周期</th>
            <th>方向</th>
            <th>信号</th>
            <th>处理</th>
            <th>执行</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in recentSignals" :key="s.id">
            <td>{{ s.id }}</td>
            <td>{{ formatDateTime(s.generated_at) }}</td>
            <td>{{ s.symbol }}</td>
            <td>{{ timeframeLabel(s.timeframe) }}</td>
            <td>{{ signalDirection(s.direction, s.closes_direction) }}</td>
            <td>{{ signalLabel(s.state) }}</td>
            <td>{{ signalStatusLabel(s.status) }}</td>
            <td>
              <button
                v-for="a in accounts"
                :key="`${s.id}-${a.id}`"
                class="ghost"
                :disabled="busy === a.id"
                @click="executeFromPanel(a, s.id)"
              >
                → {{ a.name }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">
        还没有持久化信号。到「信号」页跑一次扫描并持久化，或等定时任务产生信号。
      </p>
    </div>

    <p v-if="!accounts.length" class="muted" style="margin-top: 14px">
      还没有模拟账户，先创建一个吧。
    </p>
  </div>
</template>

<style scoped>
/* 选中的账户要在成排的行里一眼看出来（从回测页跳过来的 ?account= 也落在这一行）。
   hover 只有底色，选中另有左侧的强调条与粗体，所以两者不会混淆。 */
tbody tr.account-selected {
  background: var(--panel-2);
  font-weight: 600;
}

tbody tr.account-selected td:first-child {
  box-shadow: inset 3px 0 0 var(--accent);
}
</style>
