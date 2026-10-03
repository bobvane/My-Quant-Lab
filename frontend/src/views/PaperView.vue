<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type PaperAccount, type PaperPosition, type SignalRecord } from '@/api'
import EquityChart from '@/components/EquityChart.vue'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPaperPnlPct, formatPercent, signalDirection, toneOf } from '@/format'

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

/**
 * 回测 vs 模拟（评审 §15、ADR-130）：模拟盘是验证工具，所以它最有用的一屏是「和回测比」。
 *
 * 两个事实必须同时说：① 两列读数都来自已经存下来的东西（回测用一次已完成回测的读数，
 * 模拟用绩效端点的读数），这一页不重新计算任何指标；② 两边的时间跨度不一样长，所以这两个
 * 收益不能直接比大小 —— 时间跨度只由曲线首尾时间戳算出差多少天，用来解释这件事，而不是
 * 拿它去折算或修正收益。
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

/** 找出这个账户该跟哪一次回测比：绑定策略的当前版本、最近一次跑完的回测。 */
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

    if (account.strategy_id === null) {
      result.note =
        '这个账户没有绑定策略，所以没有可以对照的历史回测。绑定一个策略之后，这里会把两边并排放在一起。'
      comparison.value = result
      return
    }

    const versions = await api.strategyVersions(account.strategy_id)
    const version = versions.find((v) => v.is_current) ?? versions[0]
    if (!version) {
      result.note = '这个策略还没有版本记录：先到「我的策略」创建版本并跑一次回测。'
      comparison.value = result
      return
    }

    const runs = await api.backtests(version.id)
    const run = runs.find((r) => r.status === 'completed')
    if (run) {
      const detail = await api.backtest(run.id)
      result.backtest = {
        total_return: run.total_return,
        max_drawdown: run.max_drawdown,
        trades: run.number_of_trades,
        window: spanLabel(detail.equity_curve.map((p) => p.timestamp)),
        detail: `策略版本 ${version.version} 的第 #${run.id} 次回测`,
      }
    } else {
      result.note = '这个策略版本还没有跑完的回测，先在「回测」页跑一次，这里才有对照。'
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
    comparison.value = result
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    comparing.value = null
  }
}

onMounted(() => {
  void load()
  void loadRecentSignals()
})
</script>

<template>
  <div>
    <h1 class="page-title">模拟验证</h1>
    <p class="page-sub">
      这一页不是记账工具，而是策略的验证工具：让同一个策略在一段真实走过的时间里用虚拟资金成交，
      看它和回测差多少。模拟账户使用虚拟资金，与 Ghostfolio 真实持仓完全隔离。成交按「信号参考价 +
      滑点」计算并计入手续费；V1 为多头单持仓，且不包含任何券商下单接口。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="card card-quiet">
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
        :tone="toneOf(a.realized_pnl)"
        :sub="`净入金 ${formatNumber(a.net_deposits)} ${a.base_currency} · 已实现盈亏 ${formatNumber(a.realized_pnl)}（${formatPaperPnlPct(a.net_deposits, a.realized_pnl)}）`"
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
            <th>已实现盈亏</th>
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
            <td>{{ formatNumber(a.cash) }}</td>
            <td :class="toneOf(a.realized_pnl)">{{ formatNumber(a.realized_pnl) }}</td>
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
              <button class="ghost" :disabled="comparing === a.id" @click="loadComparison(a)">
                {{ comparing === a.id ? '对比中…' : '回测 vs 模拟' }}
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
            <td>{{ signalDirection(s.direction, s.closes_direction) }}</td>
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
