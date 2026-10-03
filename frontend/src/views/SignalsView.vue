<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type ExplainResult, type SignalRecord } from '@/api'
import { formatDateTime, formatNumber, formatPercent, signalDirection, toneOf } from '@/format'
import { isAdvanced } from '@/mode'
// 状态、周期、分组键都走同一个词汇表：同一件事在这一页只说一种话（ADR-127，评审 §12／§14）。
import {
  REFERENCE_PRICE_DISCLAIMER,
  SIGNAL_DISCLAIMER,
  signalLabel,
  timeframeLabel,
} from '@/wording'

const signals = ref<SignalRecord[]>([])
const stateFilter = ref('')
const symbolFilter = ref('')
const error = ref('')
const info = ref('')
const loading = ref(false)
const pageOffset = ref(0)
const PAGE = 50

function resetAndLoad() {
  pageOffset.value = 0
  return load()
}

async function loadMore() {
  pageOffset.value += PAGE
  loading.value = true
  try {
    const more = await api.signals(
      stateFilter.value || undefined,
      PAGE,
      symbolFilter.value.trim() || undefined,
      pageOffset.value,
    )
    signals.value = [...signals.value, ...more]
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
const acknowledging = ref<number | null>(null)
const explaining = ref<number | null>(null)
const explanation = ref<ExplainResult | null>(null)
const explainedFor = ref('')
const evidence = ref<Record<string, any> | null>(null)
const evidenceFor = ref('')
const evidencing = ref<number | null>(null)
const scanning = ref(false)

async function scan() {
  error.value = ''
  info.value = ''
  scanning.value = true
  try {
    const result = await api.scanSignals(true)
    await resetAndLoad()
    info.value =
      result.created > 0
        ? `扫描完成：评估 ${result.evaluated} 条，写入 ${result.created} 条新信号`
        : `扫描完成：评估 ${result.evaluated} 条，没有新的可执行信号`
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    scanning.value = false
  }
}

const outcomes = ref<Array<Record<string, any>>>([])
const outcomeSummary = ref<Record<string, any> | null>(null)
const showOutcomes = ref(false)

async function toggleOutcomes() {
  error.value = ''
  if (showOutcomes.value) {
    showOutcomes.value = false
    return
  }
  try {
    const [rows, summary] = await Promise.all([
      api.signalOutcomes(50, symbolFilter.value.trim() || undefined),
      api.signalOutcomeSummary(symbolFilter.value.trim() || undefined).catch(() => null),
    ])
    outcomes.value = rows
    outcomeSummary.value = summary
    showOutcomes.value = true
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function showEvidence(row: SignalRecord) {
  error.value = ''
  evidencing.value = row.id
  try {
    evidence.value = await api.signalEvidence(row.id)
    evidenceFor.value = `#${row.id} ${row.symbol ?? ''} ${row.state}`
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    evidencing.value = null
  }
}

// The four segments `docs/13_UI_UX.md` §6 still owed: 当前状态 / 策略历史统计 /
// 真实持仓上下文 / 风险失效条件. Nothing here invents a number: the "current" price
// is the newest *closed* bar of the local dataset, the history is the newest
// completed backtest, and a signal that has no data says so instead of showing a
// zero (ADR-112).
const detailRow = ref<SignalRecord | null>(null)
const detailBars = ref<Array<{ timestamp: string; close: number }>>([])
const detailStrategy = ref<Record<string, any> | null>(null)
const detailOutcome = ref<Record<string, any> | null>(null)
const detailNote = ref('')
const detailBarsCapped = ref(false)
const detailing = ref<number | null>(null)

const BARS_WINDOW = 120

async function showDetail(row: SignalRecord) {
  error.value = ''
  detailRow.value = row
  detailBars.value = []
  detailStrategy.value = null
  detailOutcome.value = null
  detailNote.value = ''
  detailBarsCapped.value = false
  detailing.value = row.id
  try {
    if (row.symbol) {
      const bars = await api.latestBars(row.symbol, row.timeframe, BARS_WINDOW)
      detailBars.value = (bars.bars ?? []).map((bar) => ({
        timestamp: bar.timestamp,
        close: bar.close,
      }))
      detailBarsCapped.value = detailBars.value.length >= BARS_WINDOW
    } else {
      detailNote.value = '这条信号没有代码，读不到最新已收盘 K 线。'
    }
    detailStrategy.value = await readStrategyEvidence(row)
    detailOutcome.value = await api
      .signalOutcomeSummary(row.symbol ?? undefined)
      .catch(() => null)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    detailing.value = null
  }
}

async function readStrategyEvidence(row: SignalRecord): Promise<Record<string, any> | null> {
  try {
    return await api.strategyEvidence(
      row.strategy_version_id,
      row.symbol ?? undefined,
      row.timeframe,
    )
  } catch (e) {
    detailNote.value = `策略证据读取失败：${(e as Error).message}`
    return null
  }
}

function lastClosedBar(): { timestamp: string; close: number } | null {
  return detailBars.value.length ? detailBars.value[detailBars.value.length - 1] : null
}

function changeVsReference(): number | null {
  const reference = detailRow.value?.price_reference
  const bar = lastClosedBar()
  if (reference == null || bar === null || !(reference > 0)) return null
  return (bar.close - reference) / reference
}

/** Closed bars printed after the signal's own bar (bounded by the fetch window). */
function barsSinceSignal(): number | null {
  const stamp = detailRow.value?.bar_timestamp
  if (!stamp) return null
  const at = new Date(stamp).getTime()
  if (Number.isNaN(at)) return null
  return detailBars.value.filter((bar) => new Date(bar.timestamp).getTime() > at).length
}

/** A stop / target level's distance from the signal's reference price. */
function distanceFromReference(level: number | null | undefined): number | null {
  const reference = detailRow.value?.price_reference
  if (level == null || reference == null || !(reference > 0)) return null
  return (level - reference) / reference
}

/** Ghostfolio reports `allocation_pct` in percent points: 3.4 means 3.4%. */
function formatPercentPoints(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A'
  return `${formatNumber(value, digits)}%`
}

const latestStatus = computed(() => {
  const bar = lastClosedBar()
  if (bar === null) {
    return { found: false, time: '—', close: '—', change: '—', barsSince: null, capped: false }
  }
  return {
    found: true,
    time: formatDateTime(bar.timestamp),
    close: formatNumber(bar.close),
    change: formatPercent(changeVsReference()),
    barsSince: barsSinceSignal(),
    capped: detailBarsCapped.value,
  }
})

const backtestStats = computed(() => {
  const runs = detailStrategy.value?.layer_2_empirical_stats
  const run = Array.isArray(runs) && runs.length ? runs[0] : null
  if (run === null) return { found: false, text: '' }
  return {
    found: true,
    text:
      `回测 #${run.backtest_run_id} · ${formatDateTime(run.created_at)} · 数据集 ${run.dataset_version ?? '—'} · ` +
      `样本 ${run.number_of_trades ?? '—'} 笔 · 历史胜率 ${formatPercent(run.win_rate)} · ` +
      `最大回撤 ${formatPercent(run.max_drawdown)} · 总收益 ${formatPercent(run.total_return)} · ` +
      `夏普 ${formatNumber(run.sharpe)}`,
  }
})

const paperStatsText = computed(() => {
  const stats = detailStrategy.value?.layer_3_paper_stats
  if (!stats) return ''
  return `模拟盘：已平仓 ${stats.paper_trades ?? 0} 笔 · 已实现盈亏 ${formatNumber(stats.realized_pnl)}`
})

const strategyOutcomeText = computed(() => {
  const name = detailRow.value?.strategy_name
  const groups = detailOutcome.value?.groups
  if (!name || !groups) return ''
  const bucket = groups[`strategy:${name}`]
  if (!bucket) return ''
  return (
    `信号结果（另一组样本，别和上面的回测混在一起）：已评估 ${bucket.count} 条 · ` +
    `胜率 ${formatPercent(bucket.win_rate)} · 平均 ${formatPercent(bucket.avg_pnl_pct, 3)} · ` +
    `累计 ${formatPercent(bucket.total_pnl_pct, 3)}`
  )
})

const portfolioText = computed(() => {
  const context = detailRow.value?.portfolio_context
  if (!context) return ''
  const connected = context.ghostfolio_connected
    ? 'Ghostfolio 已连接'
    : 'Ghostfolio 未配置或不可达（信号不受影响）'
  const holding = context.holding
  const held =
    holding && holding.quantity != null
      ? ` · 持有 ${holding.quantity} 股（占比 ${formatPercentPoints(holding.allocation_pct, 2)}）`
      : ''
  return `${connected} · ${context.note ?? '—'}${held}`
})

const layer4Text = computed(() => {
  const layer = detailStrategy.value?.layer_4_portfolio_context
  if (!layer) return ''
  const held = layer.holdings_for_symbol
  return (
    `策略侧组合快照：${layer.holdings_count ?? '—'} 个持仓 · 总值 ${formatNumber(layer.total_value)}` +
    (held ? ` · 本标的 ${held.quantity} 股（占比 ${formatPercentPoints(held.allocation_pct, 2)}）` : '') +
    ` · ${layer.note ?? '—'}`
  )
})

const riskLevels = computed(() => {
  const row = detailRow.value
  const show = (level: number | null | undefined) =>
    level == null
      ? '未给出'
      : `${formatNumber(level)}（相对参考价 ${formatPercent(distanceFromReference(level))}）`
  return { stop: show(row?.stop_reference), target: show(row?.target_reference) }
})

/** The narrative half of segment 7: only what an explanation actually said. */
const invalidations = computed(() => {
  const text = detailRow.value?.explanation
  const out: string[] = []
  const push = (value: unknown) => {
    if (typeof value === 'string' && value.trim()) out.push(value)
    else if (Array.isArray(value)) {
      for (const item of value) if (typeof item === 'string' && item.trim()) out.push(item)
    }
  }
  push(text?.what_could_invalidate)
  push(text?.risks)
  push(text?.risk_notes)
  return out
})

const STATES = ['', 'BUY', 'SELL', 'WAIT', 'NO_SIGNAL']

/** 结果追踪的分组键是 ``state:BUY`` 这种机器名，先说清它是哪一类分组（ADR-127）。 */
function groupLabel(key: string): string {
  if (key === 'ALL') return '全部'
  const [kind, ...rest] = key.split(':')
  const value = rest.join(':')
  if (kind === 'state') return `状态：${signalLabel(value)}`
  if (kind === 'direction') return `方向：${signalDirection(value, null)}`
  if (kind === 'timeframe') return `周期：${timeframeLabel(value)}`
  if (kind === 'strategy') return `策略：${value}`
  return key
}

async function load() {
  error.value = ''
  loading.value = true
  try {
    signals.value = await api.signals(
      stateFilter.value || undefined,
      PAGE,
      symbolFilter.value.trim() || undefined,
      pageOffset.value,
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function acknowledge(row: SignalRecord) {
  error.value = ''
  info.value = ''
  acknowledging.value = row.id
  try {
    await api.acknowledgeSignal(row.id)
    info.value = `信号 #${row.id} 已标记为已读`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    acknowledging.value = null
  }
}

async function explain(row: SignalRecord) {
  error.value = ''
  explanation.value = null
  explaining.value = row.id
  try {
    explanation.value = await api.explainSignal(row.id)
    explainedFor.value = `#${row.id} ${row.symbol ?? ''} ${row.state}`
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    explaining.value = null
  }
}

function contextNote(row: SignalRecord): string {
  return (row.portfolio_context?.note as string) ?? '—'
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">信号</h1>
    <p class="page-sub">
      信号只基于已收盘 K 线评估：看多信号 / 看空 / 退出信号 是策略此刻给出的方向，暂不确认表示入场条件还没满足。
      {{ SIGNAL_DISCLAIMER }}
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="card">
      <div class="row" style="margin-bottom: 10px">
        <button
          v-for="s in STATES"
          :key="s || 'all'"
          :class="stateFilter === s ? '' : 'ghost'"
          @click="stateFilter = s; resetAndLoad()"
        >
          {{ s ? signalLabel(s) : '全部' }}
        </button>
        <input
          v-model="symbolFilter"
          style="max-width: 160px"
          placeholder="按代码过滤（如 AAPL）"
          @keyup.enter="resetAndLoad"
        />
        <button class="ghost" @click="resetAndLoad">查询</button>
        <button class="ghost" @click="toggleOutcomes">
          {{ showOutcomes ? '收起信号结果' : '信号结果' }}
        </button>
        <span class="muted" style="margin-left: auto">
          {{ loading ? '加载中…' : `共 ${signals.length} 条` }}
        </span>
      </div>

      <table v-if="signals.length">
        <thead>
          <tr>
            <th>时间</th>
            <th>代码</th>
            <th>策略</th>
            <th>周期</th>
            <th>状态</th>
            <th>方向</th>
            <th>参考价</th>
            <th v-if="isAdvanced">触发规则</th>
            <th>组合上下文</th>
            <th>处理</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in signals" :key="s.id">
            <td class="muted">{{ formatDateTime(s.bar_timestamp) }}</td>
            <td>{{ s.symbol ?? `#${s.asset_id}` }}</td>
            <td>{{ s.strategy_name ?? '—' }} <span class="muted">v{{ s.strategy_version }}</span></td>
            <td>{{ s.timeframe }}</td>
            <td><span class="badge" :class="s.state">{{ signalLabel(s.state) }}</span></td>
            <td>{{ signalDirection(s.direction, s.closes_direction) }}</td>
            <td>{{ s.price_reference != null ? formatNumber(s.price_reference) : '—' }}</td>
            <td v-if="isAdvanced" class="muted">
              {{ (s.triggered_rules || []).join(', ') || '—' }}
            </td>
            <td class="muted">{{ contextNote(s) }}</td>
            <td>{{ s.status }}{{ s.notified_at ? ' · 已通知' : '' }}</td>
            <td>
              <button class="ghost" :disabled="evidencing === s.id" @click="showEvidence(s)">
                {{ evidencing === s.id ? '读取中…' : '证据' }}
              </button>
              <button class="ghost" :disabled="detailing === s.id" @click="showDetail(s)">
                {{ detailing === s.id ? '读取中…' : '详情' }}
              </button>
              <button class="ghost" :disabled="explaining === s.id" @click="explain(s)">
                {{ explaining === s.id ? '解释中…' : 'AI 解释' }}
              </button>
              <button
                v-if="s.status !== 'acknowledged'"
                class="ghost"
                :disabled="acknowledging === s.id"
                @click="acknowledge(s)"
              >
                标记已读
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="row">
        <p class="muted">没有信号。</p>
        <button class="ghost" :disabled="scanning" @click="scan">
          {{ scanning ? '扫描中…' : '立即扫描' }}
        </button>
        <span class="muted">或等待定时任务（每 15 分钟）。</span>
      </div>
      <div v-if="signals.length && signals.length % PAGE === 0" class="row" style="margin-top: 8px">
        <button class="ghost" :disabled="loading" @click="loadMore">
          {{ loading ? '加载中…' : '加载更多' }}
        </button>
      </div>
    </div>

    <div v-if="detailRow" class="card" style="margin-top: 14px">
      <h3>信号详情（#{{ detailRow?.id }} {{ detailRow?.symbol ?? `#${detailRow?.asset_id}` }}）</h3>
      <p class="muted">
        四段合成一张图：这条信号现在是什么状态、这个策略历史上怎么样、你的真实持仓是什么处境、
        什么情况说明它失效了。
      </p>
      <p v-if="detailNote" class="muted">{{ detailNote }}</p>

      <h4>当前状态</h4>
      <p class="muted">
        信号：{{ formatDateTime(detailRow?.bar_timestamp) }} · {{ detailRow?.state }} /
        {{ signalDirection(detailRow?.direction, detailRow?.closes_direction) }} · 参考价
        {{ detailRow?.price_reference != null ? formatNumber(detailRow?.price_reference) : '—' }}
      </p>
      <p v-if="latestStatus.found" class="muted">
        最新已收盘 K 线（本地数据集里最新的一根，不是实时报价）：{{ latestStatus.time }} · 收盘
        {{ latestStatus.close }} · 相对参考价 {{ latestStatus.change }}
        <template v-if="latestStatus.barsSince !== null">
          · 信号之后已收盘 {{ latestStatus.barsSince }} 根<template v-if="latestStatus.capped"
            >（已到 {{ BARS_WINDOW }} 根窗口上限，实际可能更多）</template
          >
        </template>
      </p>
      <p v-else class="muted">还没有可用的已收盘 K 线，因此给不出当前状态。</p>

      <h4>策略历史统计</h4>
      <p v-if="backtestStats.found" class="muted">{{ backtestStats.text }}</p>
      <p v-else class="muted">这个策略版本还没有完成的回测，所以没有历史统计（不是零，是还没有数据）。</p>
      <p v-if="paperStatsText" class="muted">{{ paperStatsText }}</p>
      <p v-if="strategyOutcomeText" class="muted">{{ strategyOutcomeText }}</p>

      <h4>真实持仓上下文</h4>
      <p v-if="portfolioText" class="muted">{{ portfolioText }}</p>
      <p v-else class="muted">
        这条信号没有留下持仓上下文（扫描时 Ghostfolio 未配置或没有记录）。
      </p>
      <p v-if="layer4Text" class="muted">{{ layer4Text }}</p>

      <h4>风险 / 失效条件</h4>
      <p class="muted">策略价位：止损 {{ riskLevels.stop }} · 目标 {{ riskLevels.target }}</p>
      <p class="muted">{{ REFERENCE_PRICE_DISCLAIMER }}</p>
      <template v-if="invalidations.length">
        <p class="muted">AI 解释点名的失效条件：</p>
        <ul class="muted">
          <li v-for="(item, index) in invalidations" :key="index">{{ item }}</li>
        </ul>
      </template>
      <p v-else class="muted">
        这条信号还没有做过 AI 解释，所以没有叙述性的失效条件；上面的价位来自策略本身，不是 AI 的判断。
      </p>
    </div>

    <div v-if="showOutcomes" class="card" style="margin-top: 14px">
      <h3>信号结果追踪</h3>
      <p class="muted">
        回填的是「信号发出后价格如何走」：PnL% 是这笔信号方向化之后的收益，MAE% 是持仓期间最大浮亏
        （最难受的时候亏到多少），MFE% 是最大浮盈（最顺利的时候赚到多少）。
      </p>
      <p v-if="outcomeSummary" class="muted">
        范围：{{ outcomeSummary.symbol ?? '全部标的' }} · 共 {{ outcomeSummary.signals }} 条信号，已评估
        {{ outcomeSummary.decided }} 条<template v-if="outcomeSummary.undecided > 0"
          >，另有 {{ outcomeSummary.undecided }} 条还没有结果（要等信号后
          {{ outcomeSummary.bars_after }} 根 K 线，或该标的还没有 K 线序列）</template
        >。
      </p>
      <div v-if="outcomeSummary?.groups?.ALL" class="row" style="margin-bottom: 8px">
        <span class="stat small">整体胜率 {{ formatPercent(outcomeSummary.groups.ALL.win_rate) }}</span>
        <span class="muted">样本 {{ outcomeSummary.groups.ALL.count }}（已评估的信号）· 平均 {{ formatPercent(outcomeSummary.groups.ALL.avg_pnl_pct, 3) }} · 累计 {{ formatPercent(outcomeSummary.groups.ALL.total_pnl_pct, 3) }}</span>
      </div>
      <table v-if="outcomeSummary && Object.keys(outcomeSummary.groups).length > 1" style="margin-bottom: 10px">
        <thead>
          <tr>
            <th>分组</th>
            <th>样本</th>
            <th>胜率</th>
            <th>平均 PnL%</th>
            <th>累计 PnL%</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(g, k) in outcomeSummary.groups" :key="k">
            <td>{{ groupLabel(String(k)) }}</td>
            <td>{{ g.count }}</td>
            <td>{{ g.win_rate != null ? formatPercent(g.win_rate) : '—' }}</td>
            <td :class="toneOf(g.avg_pnl_pct)">{{ g.avg_pnl_pct != null ? formatPercent(g.avg_pnl_pct, 3) : '—' }}</td>
            <td :class="toneOf(g.total_pnl_pct)">{{ g.total_pnl_pct != null ? formatPercent(g.total_pnl_pct, 3) : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="outcomes.length" class="muted">下表是最近 {{ outcomes.length }} 条已评估信号。</p>
      <table v-if="outcomes.length">
        <thead>
          <tr>
            <th>信号</th>
            <th>方向</th>
            <th>周期</th>
            <th>K 线时间</th>
            <th>入场时间</th>
            <th>出场时间</th>
            <th>结果</th>
            <th>PnL%</th>
            <th>MAE%</th>
            <th>MFE%</th>
            <th>评估时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="o in outcomes" :key="String(o.signal_id)">
            <td>#{{ o.signal_id }}</td>
            <td>{{ signalDirection(o.direction, o.closes_direction) }}</td>
            <td>{{ o.timeframe }}</td>
            <td class="muted">{{ formatDateTime(String(o.bar_timestamp)) }}</td>
            <td class="muted">{{ o.entry_time ? formatDateTime(String(o.entry_time)) : '—' }}</td>
            <td class="muted">{{ o.exit_time ? formatDateTime(String(o.exit_time)) : '—' }}</td>
            <td>{{ o.outcome_state }}</td>
            <td :class="toneOf(o.pnl_pct)">{{ o.pnl_pct != null ? formatPercent(o.pnl_pct, 3) : '—' }}</td>
            <td class="muted">{{ o.mae_pct != null ? formatPercent(o.mae_pct, 3) : '—' }}</td>
            <td class="muted">{{ o.mfe_pct != null ? formatPercent(o.mfe_pct, 3) : '—' }}</td>
            <td class="muted">{{ o.evaluated_at ? formatDateTime(String(o.evaluated_at)) : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">
        还没有信号结果<template v-if="outcomeSummary && outcomeSummary.signals > 0"
          >（共 {{ outcomeSummary.signals }} 条信号，还没有一条等到信号后
          {{ outcomeSummary.bars_after }} 根 K 线）</template
        ><template v-else>（需价格前进后由定时任务回填）</template>。
      </p>
    </div>

    <div v-if="evidence" class="card" style="margin-top: 14px">
      <h3>信号证据（{{ evidenceFor }}）</h3>
      <p class="muted">
        触发规则：{{ (evidence.triggered_rules || []).join(', ') || '—' }}
        <span v-if="isAdvanced">
          · 特征哈希 {{ String(evidence.feature_snapshot_hash || '').slice(0, 12) }}…</span
        >
        <span v-if="evidence.portfolio_context?.note"> · {{ evidence.portfolio_context.note }}</span>
      </p>
      <table v-if="isAdvanced && evidence.feature_snapshot?.values">
        <thead>
          <tr>
            <th>特征</th>
            <th>值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(v, k) in evidence.feature_snapshot.values" :key="k">
            <td>{{ k }}</td>
            <td>{{ v != null ? formatNumber(v, 4) : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!isAdvanced && evidence.feature_snapshot?.values" class="muted">
        这次计算用到的特征明细在高级模式里可以看到；上面的规则与上下文就是它的结论。
      </p>
      <p v-else class="muted">该信号没有特征快照（可能是较早的信号）。</p>
    </div>

    <div v-if="explanation" class="card" style="margin-top: 14px">
      <h3>AI 解释（{{ explainedFor }}）</h3>
      <p>{{ explanation.explanation.summary }}</p>
      <p class="muted">{{ explanation.explanation.plain_language }}</p>
      <ul v-if="explanation.explanation.what_to_watch_next?.length">
        <li v-for="(t, i) in explanation.explanation.what_to_watch_next" :key="i">{{ t }}</li>
      </ul>
    </div>
  </div>
</template>