<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ExplainResult, type SignalRecord } from '@/api'
import { formatDateTime, formatNumber } from '@/format'

const signals = ref<SignalRecord[]>([])
const stateFilter = ref('')
const symbolFilter = ref('')
const error = ref('')
const info = ref('')
const loading = ref(false)
const acknowledging = ref<number | null>(null)
const explaining = ref<number | null>(null)
const explanation = ref<ExplainResult | null>(null)
const explainedFor = ref('')
const evidence = ref<Record<string, any> | null>(null)
const evidenceFor = ref('')
const evidencing = ref<number | null>(null)

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
      100,
      symbolFilter.value.trim() || undefined,
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
          @click="stateFilter = s; load()"
        >
          {{ s || '全部' }}
        </button>
        <input
          v-model="symbolFilter"
          style="max-width: 160px"
          placeholder="按代码过滤（如 AAPL）"
          @keyup.enter="load"
        />
        <button class="ghost" @click="load">查询</button>
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
      <p v-else class="muted">没有信号。到「研究仪表盘」点「立即扫描」，或等待定时任务。</p>
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