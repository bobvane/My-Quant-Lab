<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type PaperAccount, type PaperPosition, type SignalRecord } from '@/api'
import EquityChart from '@/components/EquityChart.vue'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPaperPnlPct, formatPercent, toneOf } from '@/format'

const accounts = ref<PaperAccount[]>([])
const error = ref('')
const info = ref('')
const name = ref('PA Strategy')
const cash = ref(100000)
const creating = ref(false)

const signalId = ref<number | null>(null)
const fundAmount = ref(1000)
const positions = ref<PaperPosition[]>([])
const activeAccount = ref<number | null>(null)
const busy = ref<number | null>(null)
const performance = ref<Record<string, any> | null>(null)
const perfAccount = ref<number | null>(null)
const loadingPerf = ref<number | null>(null)

const equityCurve = ref<Array<{ timestamp: string; equity: number }>>([])
const equityNote = ref('')
const equityAccount = ref<number | null>(null)
const loadingEquity = ref<number | null>(null)
const recentSignals = ref<SignalRecord[]>([])
const loadingSignals = ref(false)

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

/** The most recent persisted signals, so a signal can be executed without its id. */
async function loadRecentSignals() {
  loadingSignals.value = true
  try {
    recentSignals.value = await api.signals(undefined, 10)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingSignals.value = false
  }
}

async function executeFromPanel(account: PaperAccount, id: number) {
  signalId.value = id
  await executeSignal(account)
  await loadRecentSignals()
}

async function loadPerformance(accountId: number) {
  error.value = ''
  loadingPerf.value = accountId
  try {
    performance.value = await api.paperPerformance(accountId)
    perfAccount.value = accountId
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loadingPerf.value = null
  }
}

/** Why the return metrics are missing, in the backend's own words when it says so. */
function performanceNotice(value: Record<string, any>): string {
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
  creating.value = true
  error.value = ''
  try {
    await api.createPaperAccount(name.value, cash.value)
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    creating.value = false
  }
}

async function executeSignal(account: PaperAccount) {
  error.value = ''
  info.value = ''
  if (!signalId.value) {
    error.value = '请填写信号 ID'
    return
  }
  busy.value = account.id
  try {
    const r = await api.executePaperSignal(account.id, signalId.value)
    info.value = `${account.name} 执行 ${r.side}：成交价 ${formatNumber(r.fill_price)} × ${formatNumber(
      r.quantity,
    )}（费用 ${formatNumber(r.fees)}，滑点 ${formatNumber(r.slippage)}），已实现 ${formatNumber(
      r.realized_pnl,
    )}，现金 ${formatNumber(r.cash)}`
    await load()
    await loadPositions(account.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
  }
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
    info.value = `${account.name} 状态：${r.status}`
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
    await load()
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
    info.value = `${account.name} 虚拟资金调整后现金：${formatNumber(r.cash)}`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
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

onMounted(() => {
  void load()
  void loadRecentSignals()
})
</script>

<template>
  <div>
    <h1 class="page-title">模拟盘</h1>
    <p class="page-sub">
      模拟账户使用虚拟资金，与 Ghostfolio 真实持仓完全隔离。成交按「信号参考价 + 滑点」计算并计入手续费；
      V1 为多头单持仓，且不包含任何券商下单接口。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="card">
      <h3>新建模拟账户</h3>
      <div class="row">
        <input v-model="name" style="max-width: 220px" placeholder="账户名称" />
        <input v-model.number="cash" type="number" style="max-width: 180px" />
        <button :disabled="creating" @click="create">创建</button>
      </div>
    </div>

    <div v-if="accounts.length" class="grid cols-3" style="margin-top: 14px">
      <StatCard
        v-for="a in accounts"
        :key="a.id"
        :label="a.name"
        :value="formatNumber(a.cash)"
        :tone="toneOf(a.cash - a.net_deposits)"
        :sub="`净入金 ${formatNumber(a.net_deposits)} ${a.base_currency} · 盈亏 ${formatPaperPnlPct(a.net_deposits, a.cash)}`"
      />
    </div>

    <div v-if="accounts.length" class="card" style="margin-top: 14px">
      <h3>账户明细</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>净入金</th>
            <th>当前现金</th>
            <th>状态</th>
            <th>重置次数</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in accounts" :key="a.id">
            <td>{{ a.id }}</td>
            <td>{{ a.name }}</td>
            <td>{{ formatNumber(a.net_deposits) }}</td>
            <td :class="toneOf(a.cash - a.net_deposits)">{{ formatNumber(a.cash) }}</td>
            <td>{{ a.status }}</td>
            <td>{{ a.reset_count }}</td>
            <td>{{ formatDateTime(a.created_at) }}</td>
            <td>
              <button class="ghost" @click="loadPositions(a.id)">持仓</button>
              <button class="ghost" :disabled="loadingPerf === a.id" @click="loadPerformance(a.id)">
                {{ loadingPerf === a.id ? '计算中…' : '绩效' }}
              </button>
              <button class="ghost" :disabled="loadingEquity === a.id" @click="loadEquity(a.id)">
                {{ loadingEquity === a.id ? '读取中…' : '权益曲线' }}
              </button>
              <button
                v-if="a.status === 'active'"
                class="ghost"
                :disabled="busy === a.id"
                @click="setStatus(a, 'close')"
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

    <div v-if="accounts.length" class="card" style="margin-top: 14px">
      <h3>执行信号（虚拟成交）</h3>
      <p class="muted">
        填写一个已持久化的信号 ID，对指定账户按该信号成交。BUY 开仓、SELL 平仓；账户关闭时拒绝成交。
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
    </div>

    <div v-if="activeAccount !== null" class="card" style="margin-top: 14px">
      <h3>持仓（账户 #{{ activeAccount }}）</h3>
      <table v-if="positions.length">
        <thead>
          <tr>
            <th>资产</th>
            <th>数量</th>
            <th>成本价</th>
            <th>已实现盈亏</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in positions" :key="p.id">
            <td>#{{ p.asset_id }}</td>
            <td>{{ formatNumber(p.quantity) }}</td>
            <td>{{ formatNumber(p.avg_cost) }}</td>
            <td :class="toneOf(p.realized_pnl)">{{ formatNumber(p.realized_pnl) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">该账户当前没有持仓。</p>
    </div>

    <div v-if="performance" class="card" style="margin-top: 14px">
      <h3>绩效（账户 #{{ perfAccount }}）</h3>
      <p class="muted">{{ performance.note }}</p>
      <div class="grid cols-4">
        <StatCard label="期末权益" :value="formatNumber(performance.final_equity)" sub="净入金 + 已实现" />
        <StatCard label="总收益" :value="formatPercent(performance.metrics.total_return)" :tone="toneOf(performance.metrics.total_return)" sub="已平仓" />
        <StatCard label="最大回撤" :value="formatPercent(performance.metrics.max_drawdown)" :tone="toneOf(performance.metrics.max_drawdown)" sub="越小越好" />
        <StatCard label="胜率" :value="formatPercent(performance.metrics.win_rate)" :sub="`交易 ${performance.closed_trades} 次`" />
      </div>
      <p v-if="performance.metrics.total_return === null" class="muted" style="margin-top: 10px">
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
      <p class="muted">最近 10 条已持久化的信号。可以直接对某个账户执行，不必手抄信号 ID。</p>
      <p v-if="loadingSignals" class="muted">读取中…</p>
      <table v-else-if="recentSignals.length">
        <thead>
          <tr>
            <th>ID</th>
            <th>生成时间</th>
            <th>资产</th>
            <th>周期</th>
            <th>方向</th>
            <th>状态</th>
            <th>执行</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in recentSignals" :key="s.id">
            <td>{{ s.id }}</td>
            <td>{{ formatDateTime(s.generated_at) }}</td>
            <td>{{ s.symbol }}</td>
            <td>{{ s.timeframe }}</td>
            <td>{{ s.direction }}</td>
            <td>{{ s.state }}</td>
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
