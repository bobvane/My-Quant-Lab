<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AIStatus, type ExplainResult, type HealthResponse, type PaperAccount, type SignalIntent, type SystemInfo } from '@/api'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'

const health = ref<HealthResponse | null>(null)
const info = ref<SystemInfo | null>(null)
const signals = ref<SignalIntent[]>([])
const accounts = ref<PaperAccount[]>([])
const scanning = ref(false)
const error = ref('')
const aiStatus = ref<AIStatus | null>(null)
const explaining = ref<number | null>(null)
const explanation = ref<ExplainResult | null>(null)
const explainedFor = ref('')
const gfHoldings = ref<Array<Record<string, any>>>([])
const gfSummary = ref<Record<string, any> | null>(null)
const gfConnected = ref(false)

async function load() {
  error.value = ''
  try {
    const [h, i, a, ai, gf] = await Promise.all([
      api.health(),
      api.systemInfo(),
      api.paperAccounts(),
      api.aiStatus().catch(() => null),
      api.getGhostfolioHoldings().catch(() => null),
    ])
    health.value = h
    info.value = i
    accounts.value = a
    aiStatus.value = ai
    if (gf?.holdings) {
      gfHoldings.value = gf.holdings
      gfSummary.value = gf
      gfConnected.value = true
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function runScan() {
  scanning.value = true
  error.value = ''
  try {
    const result = await api.scanSignals()
    signals.value = result.signals
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    scanning.value = false
  }
}

const actionable = () => signals.value.filter((s) => s.state === 'BUY' || s.state === 'SELL').length
const waiting = () => signals.value.filter((s) => s.state === 'WAIT').length

async function explainRow(index: number) {
  const s = signals.value[index]
  if (!s || s.strategy_version_id == null) return
  explaining.value = index
  error.value = ''
  try {
    explanation.value = await api.explainSignalPreview(
      s.strategy_version_id,
      s.symbol ?? undefined,
      s.timeframe ?? '1d',
    )
    explainedFor.value = `${s.symbol ?? ''} ${s.timeframe ?? ''} ${s.state}`
  } catch (e) {
    error.value = (e as Error).message
    explanation.value = null
  } finally {
    explaining.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">研究仪表盘</h1>
    <p class="page-sub">
      这里只展示量化引擎已经算好的结果。AI 负责解释，不负责计算；本系统不连接券商，不会自动下单。
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="grid cols-4">
      <StatCard
        label="系统状态"
        :value="health?.status ?? '—'"
        :sub="health ? `数据库 ${health.database} / Redis ${health.redis}` : '连接中…'"
      />
      <StatCard label="版本" :value="health?.version ?? '—'" :sub="`引擎 ${health?.engine_version ?? '—'}`" />
      <StatCard
        label="可执行信号"
        :value="signals.length ? actionable() : '—'"
        sub="BUY / SELL（仅信息提醒）"
      />
      <StatCard
        label="观察中"
        :value="signals.length ? waiting() : '—'"
        sub="WAIT：条件未确认，不追单"
      />
    </div>

    <div v-if="gfConnected && gfHoldings.length" class="card" style="margin-top: 14px">
      <h3>我的 Ghostfolio 持仓</h3>
      <div class="row" style="align-items: flex-start; gap: 18px; flex-wrap: nowrap">
        <div style="flex: 1 1 auto; min-width: 0; overflow-x: auto">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>数量</th>
            <th>成本价</th>
            <th>现价</th>
            <th>市值</th>
            <th>占比</th>
            <th>未实现盈亏</th>
            <th>盈亏%</th>
            <th>首次建仓</th>
            <th>年化股息</th>
            <th>股息率</th>
            <th>最近派息日</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in gfHoldings" :key="h.symbol">
            <td>{{ h.symbol }}</td>
            <td>{{ h.name }}</td>
            <td>{{ formatNumber(h.quantity, 4) }}</td>
            <td>{{ h.cost_per_share != null ? formatNumber(h.cost_per_share) : '—' }}</td>
            <td>{{ h.price != null ? formatNumber(h.price) : '—' }}</td>
            <td>{{ formatNumber(h.value) }}</td>
            <td>{{ formatNumber(h.allocation_pct, 1) }}%</td>
            <td :class="toneOf(h.unrealized_pnl)">
              {{ h.unrealized_pnl != null ? formatNumber(h.unrealized_pnl) : '—' }}
            </td>
            <td :class="toneOf(h.unrealized_pnl_pct)">
              {{ h.unrealized_pnl_pct != null ? formatNumber(h.unrealized_pnl_pct, 2) + '%' : '—' }}
            </td>
            <td class="muted">{{ h.first_activity_date ? String(h.first_activity_date).slice(0, 10) : '—' }}</td>
            <td>{{ h.annual_dividend_per_share != null ? formatNumber(h.annual_dividend_per_share) : '—' }}</td>
            <td>{{ h.dividend_yield_pct != null ? formatNumber(h.dividend_yield_pct, 2) + '%' : '—' }}</td>
            <td class="muted">{{ h.last_dividend_date ? String(h.last_dividend_date).slice(0, 10) : '—' }}</td>
          </tr>
        </tbody>
      </table>
        </div>
        <div style="flex: 0 0 230px">
          <div class="card" style="padding: 10px 12px">
            <h3 style="margin-bottom: 8px">组合概览</h3>
            <div class="row" style="justify-content: space-between">
              <span class="muted">总市值</span>
              <span class="stat small">
                {{ gfSummary?.total_value != null ? formatNumber(gfSummary.total_value) : '—' }}
              </span>
            </div>
            <div class="row" style="justify-content: space-between">
              <span class="muted">总成本</span>
              <span>{{ gfSummary?.total_cost != null ? formatNumber(gfSummary.total_cost) : '—' }}</span>
            </div>
            <div class="row" style="justify-content: space-between">
              <span class="muted">总体盈利</span>
              <span :class="toneOf(gfSummary?.total_pnl)">
                {{ gfSummary?.total_pnl != null ? formatNumber(gfSummary.total_pnl) : '—' }}
              </span>
            </div>
            <div class="row" style="justify-content: space-between">
              <span class="muted">总涨幅</span>
              <span :class="toneOf(gfSummary?.total_pnl_pct)">
                {{
                  gfSummary?.total_pnl_pct != null
                    ? formatNumber(gfSummary.total_pnl_pct, 2) + '%'
                    : '—'
                }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="grid cols-2" style="margin-top: 14px">
      <div class="card">
        <h3>信号扫描（只用已收盘 K 线）</h3>
        <div class="row" style="margin-bottom: 10px">
          <button :disabled="scanning" @click="runScan">
            {{ scanning ? '扫描中…' : '立即扫描' }}
          </button>
          <span class="muted">共评估 {{ signals.length }} 个组合</span>
        </div>
        <table v-if="signals.length">
          <thead>
            <tr>
              <th>标的</th>
              <th>周期</th>
              <th>状态</th>
              <th>方向</th>
              <th>参考价</th>
              <th>止损</th>
              <th>目标</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in signals" :key="i">
              <td>{{ s.symbol ?? '—' }}</td>
              <td>{{ s.timeframe ?? '—' }}</td>
              <td><span class="badge" :class="s.state">{{ s.state }}</span></td>
              <td>{{ s.direction }}</td>
              <td>{{ formatNumber(s.price_reference) }}</td>
              <td>{{ formatNumber(s.stop_reference) }}</td>
              <td>{{ formatNumber(s.target_reference) }}</td>
              <td>
                <button
                  v-if="s.strategy_version_id != null"
                  class="ghost"
                  :disabled="explaining !== null"
                  @click="explainRow(i)"
                >
                  {{ explaining === i ? '解释中…' : 'AI 解释' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没有扫描结果。先到「行情与策略」同步数据并创建策略，再回来扫描。</p>
        <p v-if="aiStatus && !aiStatus.configured" class="muted" style="margin-top: 8px">
          AI 未配置：解释按钮不可用（{{ aiStatus.note }}）。量化功能不受影响。
        </p>
        <div v-if="explanation" class="notice" style="margin-top: 10px">
          <strong>AI 解释 · {{ explainedFor }}</strong>
          <span v-if="explanation.cached" class="muted">（缓存命中，未产生费用）</span>
          <p>{{ explanation.explanation.summary }}</p>
          <p class="muted">{{ explanation.explanation.plain_language }}</p>
          <ul v-if="explanation.explanation.why?.length" class="muted">
            <li v-for="(w, idx) in explanation.explanation.why" :key="idx">{{ w }}</li>
          </ul>
          <p v-if="explanation.explanation.risk_notes?.length" class="muted">
            风险提示：{{ explanation.explanation.risk_notes.join('；') }}
          </p>
        </div>
      </div>

      <div class="card">
        <h3>模拟账户（与真实持仓完全隔离）</h3>
        <table v-if="accounts.length">
          <thead>
            <tr>
              <th>名称</th>
              <th>初始资金</th>
              <th>当前现金</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in accounts" :key="a.id">
              <td>{{ a.name }}</td>
              <td>{{ formatNumber(a.initial_cash) }} {{ a.base_currency }}</td>
              <td :class="toneOf(a.cash - a.initial_cash)">
                {{ formatNumber(a.cash) }}
                <span class="muted">({{ formatPercent((a.cash - a.initial_cash) / a.initial_cash) }})</span>
              </td>
              <td>{{ a.status }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">尚未创建模拟账户。</p>
        <p class="notice" style="margin-top: 12px">
          模拟账户使用虚拟资金，与 Ghostfolio 真实持仓严格隔离，任何操作都不会影响真实账户。
        </p>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>系统构成</h3>
      <div class="row">
        <span v-for="m in info?.modules ?? []" :key="m" class="badge">{{ m }}</span>
      </div>
      <p class="muted" style="margin-top: 10px">
        行情源：{{ info?.market_data_provider ?? '—' }} · 特征版本：{{ info?.feature_version ?? '—' }} ·
        DSL Schema：{{ info?.strategy_schema_version ?? '—' }}
      </p>
    </div>
  </div>
</template>
