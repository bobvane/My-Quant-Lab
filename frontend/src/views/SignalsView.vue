<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ExplainResult, type SignalRecord } from '@/api'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'

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

const STATES = ['', 'BUY', 'SELL', 'WAIT', 'NO_SIGNAL']

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
      信号只基于已收盘 K 线评估。BUY/SELL 为可操作状态，WAIT 表示入场条件部分满足，
      NO_SIGNAL 为无事件。信号是研究信息，不会自动下单。
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
          {{ s || '全部' }}
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
            <th>触发规则</th>
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
            <td><span class="badge" :class="s.state">{{ s.state }}</span></td>
            <td>{{ s.direction }}</td>
            <td>{{ s.price_reference != null ? formatNumber(s.price_reference) : '—' }}</td>
            <td class="muted">{{ (s.triggered_rules || []).join(', ') || '—' }}</td>
            <td class="muted">{{ contextNote(s) }}</td>
            <td>{{ s.status }}{{ s.notified_at ? ' · 已通知' : '' }}</td>
            <td>
              <button class="ghost" :disabled="evidencing === s.id" @click="showEvidence(s)">
                {{ evidencing === s.id ? '读取中…' : '证据' }}
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

    <div v-if="showOutcomes" class="card" style="margin-top: 14px">
      <h3>信号结果追踪</h3>
      <p class="muted">
        回填的是「信号发出后价格如何走」：pnl% 为方向化收益，MAE/MFE 为最大不利/有利偏移。
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
            <td>{{ k }}</td>
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
            <td>{{ o.direction }}</td>
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
        触发规则：{{ (evidence.triggered_rules || []).join(', ') || '—' }} ·
        特征哈希 {{ String(evidence.feature_snapshot_hash || '').slice(0, 12) }}…
        <span v-if="evidence.portfolio_context?.note"> · {{ evidence.portfolio_context.note }}</span>
      </p>
      <table v-if="evidence.feature_snapshot?.values">
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