<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type BacktestDetail, type BacktestSummary } from '@/api'
import EquityChart from '@/components/EquityChart.vue'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'

const runs = ref<BacktestSummary[]>([])
const detail = ref<BacktestDetail | null>(null)
const error = ref('')
const busy = ref(false)

async function load() {
  error.value = ''
  try {
    runs.value = await api.backtests()
    if (runs.value.length) {
      detail.value = await api.backtest(runs.value[0].id)
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function open(id: number) {
  error.value = ''
  busy.value = true
  try {
    detail.value = await api.backtest(id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">回测实验室</h1>
    <p class="page-sub">
      同一「策略版本 + 数据集 + 参数 + 引擎版本 + 特征版本」必然得到相同结果。样本不足的指标显示 N/A，不会编造。
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <div v-if="detail" class="grid cols-4">
      <StatCard
        label="总收益率"
        :value="formatPercent(detail.total_return)"
        :tone="toneOf(detail.total_return)"
        :sub="`期末权益 ${formatNumber(detail.final_equity)}`"
      />
      <StatCard
        label="最大回撤"
        :value="formatPercent(detail.max_drawdown)"
        :tone="toneOf(detail.max_drawdown)"
        sub="越小越好"
      />
      <StatCard
        label="夏普比率"
        :value="formatNumber(detail.sharpe)"
        :tone="toneOf(detail.sharpe)"
        sub="风险调整后收益"
      />
      <StatCard
        label="胜率"
        :value="formatPercent(detail.win_rate)"
        :sub="`交易 ${detail.number_of_trades ?? 0} 次`"
      />
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>权益曲线</h3>
      <EquityChart :points="detail.equity_curve" />
    </div>

    <div class="grid cols-2" style="margin-top: 14px">
      <div class="card">
        <h3>回测记录</h3>
        <table v-if="runs.length">
          <thead>
            <tr>
              <th>#</th>
              <th>时间</th>
              <th>收益</th>
              <th>回撤</th>
              <th>夏普</th>
              <th>交易</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id">
              <td>{{ r.id }}</td>
              <td>{{ formatDateTime(r.created_at) }}</td>
              <td :class="toneOf(r.total_return)">{{ formatPercent(r.total_return) }}</td>
              <td>{{ formatPercent(r.max_drawdown) }}</td>
              <td>{{ formatNumber(r.sharpe) }}</td>
              <td>{{ r.number_of_trades ?? 'N/A' }}</td>
              <td>
                <button class="ghost" :disabled="busy" @click="open(r.id)">查看</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没有回测记录。到「行情与策略」创建策略后即可运行。</p>
      </div>

      <div class="card">
        <h3>结果可复现性</h3>
        <table v-if="detail">
          <tbody>
            <tr>
              <td>结果哈希</td>
              <td><code>{{ detail.result_hash }}</code></td>
            </tr>
            <tr>
              <td>数据集哈希</td>
              <td><code>{{ detail.dataset_hash }}</code></td>
            </tr>
            <tr>
              <td>引擎版本</td>
              <td>{{ detail.engine_version }}</td>
            </tr>
            <tr>
              <td>特征版本</td>
              <td>{{ detail.feature_version }}</td>
            </tr>
            <tr>
              <td>成交模型</td>
              <td>
                {{ detail.execution_model.fill_model }} · 手续费
                {{ detail.execution_model.fee_bps }}bps · 滑点
                {{ detail.execution_model.slippage_bps }}bps
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">选择一条回测记录查看详情。</p>
      </div>
    </div>

    <div v-if="detail?.trades.length" class="card" style="margin-top: 14px">
      <h3>交易明细</h3>
      <table>
        <thead>
          <tr>
            <th>方向</th>
            <th>开仓时间</th>
            <th>开仓价</th>
            <th>平仓时间</th>
            <th>平仓价</th>
            <th>数量</th>
            <th>盈亏</th>
            <th>原因</th>
            <th>歧义成交</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in detail.trades" :key="i">
            <td>{{ t.direction }}</td>
            <td>{{ formatDateTime(String(t.entry_time)) }}</td>
            <td>{{ formatNumber(Number(t.entry_price)) }}</td>
            <td>{{ formatDateTime(t.exit_time ? String(t.exit_time) : null) }}</td>
            <td>{{ formatNumber(t.exit_price ? Number(t.exit_price) : null) }}</td>
            <td>{{ formatNumber(Number(t.quantity), 4) }}</td>
            <td :class="toneOf(t.pnl ? Number(t.pnl) : null)">
              {{ formatNumber(t.pnl ? Number(t.pnl) : null) }}
            </td>
            <td>{{ t.exit_reason }}</td>
            <td>
              <span v-if="t.ambiguous_fill" class="badge SELL">保守处理</span>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
