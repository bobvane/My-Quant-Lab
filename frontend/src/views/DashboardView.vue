<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  api,
  type AIStatus,
  type BacktestSummary,
  type ExplainResult,
  type HealthResponse,
  type PaperAccount,
  type SignalIntent,
  type SignalRecord,
  type Strategy,
  type StrategyLifecycle,
  type StrategyVersion,
  type SystemInfo,
} from '@/api'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPaperPnlPct, formatPercent, signalDirection, toneOf } from '@/format'
import { isAdvanced } from '@/mode'
import {
  REFERENCE_PRICE_DISCLAIMER,
  SIGNAL_DISCLAIMER,
  nextStepText,
  qualityLabel,
  signalLabel,
  stageLabel,
  stagePage,
  timeframeLabel,
} from '@/wording'

const health = ref<HealthResponse | null>(null)
const healthError = ref('')
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
const gfTesting = ref(false)
const gfTestResult = ref<Record<string, any> | null>(null)
const assets = ref<Array<Record<string, any>>>([])
const seriesList = ref<Array<Record<string, any>>>([])

// 研究中的那一个策略，和它的版本 / 回测 / 生命周期（ADR-127）。
const strategies = ref<Strategy[]>([])
const focusStrategy = ref<Strategy | null>(null)
const focusVersions = ref<StrategyVersion[]>([])
const focusRuns = ref<BacktestSummary[]>([])
const focusLifecycle = ref<StrategyLifecycle | null>(null)
// 已记录的历史信号（`/signals` 的账本行），和 `signals` 那个「刚扫描出来的意图」
// 不是同一种东西：账本行用 `bar_timestamp` 而不是 `bar_time`（ADR-127）。
const recentSignals = ref<SignalRecord[]>([])

function assetSymbol(assetId: number): string {
  return assets.value.find((a) => Number(a.id) === assetId)?.symbol ?? `#${assetId}`
}

function qualityTone(status: string): string {
  if (status === 'valid') return 'pos'
  if (status === 'invalid') return 'neg'
  return ''
}

async function load() {
  error.value = ''
  healthError.value = ''
  // /health asks PostgreSQL, Redis and the Celery workers, so on a bare install
  // it can take seconds. It fills its own card when it arrives instead of
  // holding the accounts table (and the whole page) hostage (ADR-069).
  void api
    .health()
    .then((h) => {
      health.value = h
    })
    .catch(() => {
      health.value = null
      healthError.value = '健康检查没有响应'
    })
  try {
    // Every panel here answers for itself: one module that fails must not blank
    // the other five (ADR-088). The failed labels are named in the banner.
    const failures: string[] = []
    const note = (label: string) => {
      failures.push(label)
      return null
    }
    const [i, a, ai, assetsList, sList, appSettings, strategyRows, recent] = await Promise.all([
      api.systemInfo().catch(() => note('系统信息')),
      api.paperAccounts().catch(() => note('模拟账户') ?? []),
      api.aiStatus().catch(() => null),
      api.assets().catch(() => []),
      api.series().catch(() => []),
      api.settings().catch(() => null),
      api.strategies().catch(() => note('策略列表') ?? []),
      api.signals(undefined, 50).catch(() => []),
    ])
    info.value = i
    accounts.value = a
    aiStatus.value = ai
    assets.value = assetsList
    seriesList.value = sList
    strategies.value = strategyRows
    recentSignals.value = recent

    // 首页第一问「我在研究什么」必须先有答案，后面三问才有主语：先定下研究中的
    // 策略（最新创建的那个），再问它的当前版本、这份版本的最近一次回测，以及生命
    // 周期走到了哪一步。三次请求各自失败得很安静：一个还没开始研究的账户也要能
    // 打开首页，而不是先看到一片报错（ADR-088）。
    const focus =
      [...strategies.value].sort(
        (x, y) => y.created_at.localeCompare(x.created_at) || y.id - x.id,
      )[0] ?? null
    focusStrategy.value = focus
    if (focus) {
      focusVersions.value = await api.strategyVersions(focus.id).catch(() => {
        note('策略版本')
        return []
      })
      const version =
        focusVersions.value.find((v) => v.is_current) ?? focusVersions.value[0] ?? null
      if (version) {
        focusRuns.value = await api.backtests(version.id).catch(() => {
          note('回测结果')
          return []
        })
      }
      focusLifecycle.value = await api.lifecycle(focus.id).catch(() => {
        note('策略进度')
        return null
      })
    }

    if (failures.length) {
      error.value = `${failures.join('、')} 加载失败，页面其余内容仍然可用`
    }
    // Only ask for holdings when Ghostfolio is configured. Asking anyway makes
    // an unconfigured optional integration answer 502 on every page load, which
    // the browser reports as a failed request (ADR-067).
    const gf = appSettings?.environment.ghostfolio_configured
      ? await api.getGhostfolioHoldings().catch(() => null)
      : null
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

// --- 首页只回答的四个问题（ADR-127，评审 §5）-----------------------------------

const latestRun = computed<BacktestSummary | null>(() => focusRuns.value[0] ?? null)

const researchSymbol = computed(
  () =>
    latestRun.value?.symbol ??
    (seriesList.value.length ? assetSymbol(Number(seriesList.value[0].asset_id)) : ''),
)

const researchTimeframe = computed(
  () => latestRun.value?.timeframe ?? String(seriesList.value[0]?.timeframe ?? '1d'),
)

/** ① 我在研究什么：标的 · 周期 · 策略名，像一句话而不是三个 id。 */
const researchFocus = computed(() => {
  const strategy = focusStrategy.value
  if (!strategy) return ''
  return [researchSymbol.value, timeframeLabel(researchTimeframe.value), strategy.name]
    .filter((part) => !!part)
    .join(' · ')
})

const researchSub = computed(() => {
  const strategy = focusStrategy.value
  if (!strategy) return ''
  const parts = [`当前阶段：${stageLabel(focusLifecycle.value?.current ?? strategy.lifecycle)}`]
  parts.push(focusRuns.value.length ? `已经回测 ${focusRuns.value.length} 次` : '还没有回测记录')
  return parts.join(' · ')
})

/** 这串数据有多长：既能说清结论覆盖的时间，也是「样本偏短」提醒的依据。 */
const dataSpanYears = computed(() => {
  const rows = seriesList.value
  const row =
    rows.find((item) => assetSymbol(Number(item.asset_id)) === researchSymbol.value) ?? rows[0]
  const start = Date.parse(String(row?.series_start ?? ''))
  const end = Date.parse(String(row?.series_end ?? ''))
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null
  return (end - start) / (365.25 * 24 * 60 * 60 * 1000)
})

/** ② 最近一次研究结论：一句话，加上决定这句话可信度的几个数。 */
const conclusion = computed(() => {
  const run = latestRun.value
  if (!run) return null
  if (run.status !== 'completed') {
    return {
      headline: `最近一次回测还没有跑完（状态：${run.status}）。`,
      detail: '跑完以后，这里会用一句话说清它过去的表现。',
    }
  }
  const years = dataSpanYears.value
  const period = years === null ? '这段历史数据' : `这段约 ${years.toFixed(1)} 年的历史数据`
  const verdict =
    run.total_return === null
      ? '这次回测没有算出总收益'
      : run.total_return >= 0
        ? `这套策略在${period}里整体是赚钱的`
        : `这套策略在${period}里整体是亏钱的`
  const oosRuns = Number(focusLifecycle.value?.evidence?.oos_runs ?? 0)
  return {
    headline: `${verdict}：累计收益 ${formatPercent(run.total_return)}，最大回撤 ${formatPercent(run.max_drawdown)}。`,
    detail: [
      `胜率 ${formatPercent(run.win_rate)}`,
      `交易 ${run.number_of_trades ?? '—'} 笔`,
      '历史回测：已完成',
      `样本外验证：${oosRuns > 0 ? '已完成' : '未完成'}`,
    ].join(' · '),
  }
})

const conclusionMissing = computed(() =>
  focusStrategy.value
    ? '这个策略还没有回测结果。到「回测」运行一次，结论会出现在这里。'
    : '还没有可以研究的东西：先到「我的策略」创建或导入一个策略。',
)

/** ③ 下一步：生命周期有证据支撑的那一步，没有就退回主流程的下一步。 */
const nextStep = computed(() => {
  const lifecycle = focusLifecycle.value
  const stage = lifecycle?.suggested_next
  if (stage) {
    return {
      text: nextStepText(stage),
      reason: lifecycle?.blocked_reason ? `现在还不能推进：${lifecycle.blocked_reason}` : '',
      to: stagePage(stage)?.to ?? '',
      linkText: stagePage(stage)?.label ?? '',
    }
  }
  if (focusStrategy.value && latestRun.value) {
    return { text: nextStepText('backtested'), reason: '', to: '/backtest', linkText: '去「回测」' }
  }
  if (focusStrategy.value) {
    return {
      text: nextStepText('validated'),
      reason: '',
      to: '/backtest',
      linkText: '去「回测」',
    }
  }
  return {
    text: '先到「我的策略」创建或导入一个策略',
    reason: '',
    to: '/market',
    linkText: '去「我的策略」',
  }
})

/** ④ 需要你注意的事情：能算出来的都算出来，算不出来的不编。 */
const warnings = computed<string[]>(() => {
  const strategy = focusStrategy.value
  if (!strategy) return []
  const list: string[] = []
  const run = latestRun.value
  if (!run) {
    list.push('这个策略还没有回测结果：现在对它的一切判断都还没有依据。')
  } else if (run.status === 'completed') {
    const trades = run.number_of_trades ?? 0
    if (trades < 30) {
      list.push(`这次回测只有 ${trades} 笔交易，样本偏少，收益率先别当真。`)
    }
    if ((run.max_drawdown ?? 0) < -0.2) {
      list.push(
        `历史最大回撤 ${formatPercent(run.max_drawdown)}：账户曾经一度跌掉这么多，先确认自己拿得住。`,
      )
    }
    if ((run.total_return ?? 0) < 0) {
      list.push('这段历史里整体是亏的：先弄清它靠什么赚钱，再谈要不要用。')
    }
  }
  const years = dataSpanYears.value
  if (years !== null && years < 5) {
    list.push(`当前数据只有 ${years.toFixed(1)} 年，样本比较短，结论的把握要打折。`)
  }
  const oosRuns = Number(focusLifecycle.value?.evidence?.oos_runs ?? 0)
  if (run?.status === 'completed' && oosRuns === 0) {
    list.push('还没有做样本外验证：目前的结果只能说明它「过去」有效。')
  }
  if (focusLifecycle.value?.degraded) {
    list.push(`这个策略已被标为降级：${focusLifecycle.value.degrade_reason ?? '没有写明原因'}。`)
  }
  if (focusLifecycle.value?.blocked_reason) {
    list.push(`推进被挡住：${focusLifecycle.value.blocked_reason}`)
  }
  const newest = recentSignals.value.reduce<SignalRecord | null>((latest, signal) => {
    if (!signal.bar_timestamp) return latest
    if (!latest?.bar_timestamp) return signal
    return Date.parse(signal.bar_timestamp) > Date.parse(latest.bar_timestamp) ? signal : latest
  }, null)
  const barTime = Date.parse(String(newest?.bar_timestamp ?? ''))
  if (newest && Number.isFinite(barTime)) {
    const months = (Date.now() - barTime) / (30.44 * 24 * 60 * 60 * 1000)
    if (months >= 6) {
      list.push(
        `最近一次信号是 ${formatDateTime(newest.bar_timestamp)}，已经 ${Math.floor(months)} 个月没有新信号了。`,
      )
    }
  }
  return list
})

async function testGf() {
  error.value = ''
  gfTestResult.value = null
  gfTesting.value = true
  try {
    gfTestResult.value = await api.testGhostfolio()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    gfTesting.value = false
  }
}

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
    explainedFor.value = `${s.symbol ?? ''} ${s.timeframe ?? ''} ${signalLabel(s.state)}`
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
    <h1 class="page-title">研究首页</h1>
    <p class="page-sub">
      这一页只回答四件事：你在研究什么、最近一次结论是什么、下一步做什么、有什么要注意的。
      更细的工程读数都在「系统管理」和高级模式里，不影响这里的判断。
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="grid cols-2 answers">
      <div class="card">
        <h3>① 我在研究什么</h3>
        <p v-if="researchFocus" class="answer-main">{{ researchFocus }}</p>
        <p v-else class="muted">还没有策略可选。到「我的策略」创建或导入一个，再回到这里。</p>
        <p v-if="researchFocus && researchSub" class="muted">{{ researchSub }}</p>
      </div>

      <div class="card answer-conclusion">
        <h3>② 最近一次研究结论</h3>
        <p v-if="conclusion" class="answer-main">{{ conclusion.headline }}</p>
        <p v-else class="muted">{{ conclusionMissing }}</p>
        <p v-if="conclusion?.detail" class="muted">{{ conclusion.detail }}</p>
      </div>

      <div class="card">
        <h3>③ 下一步</h3>
        <p class="answer-main">{{ nextStep.text }}</p>
        <p v-if="nextStep.reason" class="muted">{{ nextStep.reason }}</p>
        <p v-if="nextStep.to">
          <RouterLink :to="nextStep.to">{{ nextStep.linkText }}</RouterLink>
        </p>
      </div>

      <div class="card">
        <h3>④ 需要你注意的事情</h3>
        <ul v-if="warnings.length" class="answer-list">
          <li v-for="(item, index) in warnings" :key="index">{{ item }}</li>
        </ul>
        <p v-else class="muted">暂时没有需要特别注意的事情。</p>
      </div>
    </div>

    <div class="grid" :class="isAdvanced ? 'cols-4' : 'cols-2'" style="margin-top: 14px">
      <StatCard
        label="可执行信号"
        :value="signals.length ? actionable() : '—'"
        sub="看多 / 看空信号（仅为研究提醒，不会下单）"
      />
      <StatCard
        label="观察中"
        :value="signals.length ? waiting() : '—'"
        sub="暂不确认：入场条件还没满足，不追单"
      />
      <StatCard
        v-if="isAdvanced"
        label="系统状态"
        :value="health?.status ?? '—'"
        :sub="health ? `数据库 ${health.database} / Redis ${health.redis}` : healthError || '连接中…'"
      />
      <StatCard
        v-if="isAdvanced"
        label="版本"
        :value="health?.version ?? '—'"
        :sub="`引擎 ${health?.engine_version ?? '—'}`"
      />
    </div>

    <div v-if="gfConnected && gfHoldings.length" class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">我的 Ghostfolio 持仓</h3>
        <button class="ghost" :disabled="gfTesting" @click="testGf">
          {{ gfTesting ? '测试中…' : '测试连接' }}
        </button>
      </div>
      <p v-if="gfTestResult" :class="gfTestResult.ok ? 'notice' : 'error'" style="margin: 8px 0 0">
        连接{{ gfTestResult.ok ? '成功' : '失败' }}：{{ gfTestResult.detail }}
      </p>
      <div class="row" style="align-items: flex-start; gap: 18px; flex-wrap: nowrap; margin-top: 10px">
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

    <div v-if="seriesList.length" class="card" style="margin-top: 14px">
      <h3>数据健康</h3>
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>周期</th>
            <th>质量</th>
            <th>数据范围</th>
            <th>最后同步</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in seriesList" :key="String(s.id)">
            <td>{{ assetSymbol(Number(s.asset_id)) }}</td>
            <td>{{ timeframeLabel(String(s.timeframe)) }}</td>
            <td :class="qualityTone(String(s.quality_status))">
              {{ qualityLabel(String(s.quality_status)) }}
              <span v-if="isAdvanced" class="muted">（{{ s.quality_status }}）</span>
            </td>
            <td class="muted">
              {{ String(s.series_start ?? '').slice(0, 10) }} → {{ String(s.series_end ?? '').slice(0, 10) }}
            </td>
            <td class="muted">{{ formatDateTime(String(s.last_sync_at ?? '')) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="grid cols-2" style="margin-top: 14px">
      <div class="card">
        <h3>信号扫描（只用已收盘 K 线）</h3>
        <p class="muted">{{ SIGNAL_DISCLAIMER }}</p>
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
              <td>{{ timeframeLabel(s.timeframe) }}</td>
              <td><span class="badge" :class="s.state">{{ signalLabel(s.state) }}</span></td>
              <td>{{ signalDirection(s.direction, s.closes_direction) }}</td>
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
        <p v-if="signals.length" class="muted">{{ REFERENCE_PRICE_DISCLAIMER }}</p>
        <p v-else class="muted">还没有扫描结果。先到「我的策略」同步数据并创建策略，再回来扫描。</p>
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
        <h3>模拟验证（与真实持仓完全隔离）</h3>
        <table v-if="accounts.length">
          <thead>
            <tr>
              <th>名称</th>
              <th>净入金</th>
              <th>当前现金</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in accounts" :key="a.id">
              <td>{{ a.name }}</td>
              <td>{{ formatNumber(a.net_deposits) }} {{ a.base_currency }}</td>
              <td :class="toneOf(a.realized_pnl)">
                {{ formatNumber(a.cash) }}
                <span class="muted"
                  >（已实现盈亏 {{ formatNumber(a.realized_pnl) }} ·
                  {{ formatPaperPnlPct(a.net_deposits, a.realized_pnl) }}）</span
                >
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

    <div v-if="isAdvanced" class="card" style="margin-top: 14px">
      <h3>系统构成（高级模式）</h3>
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
