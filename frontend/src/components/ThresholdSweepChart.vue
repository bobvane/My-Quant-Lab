<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

/**
 * Vote-threshold sweep for an ensemble (`POST /research/ensemble/sweep`, docs/24 §7).
 *
 * The weighted vote is a sum of member weights, so it can only land on coalition
 * totals: between two of them the ensemble behaves *identically*. That makes this
 * surface a staircase, not a curve, which is why the return line is drawn with
 * `step: 'end'` — the value genuinely holds until the next attainable vote, it is not
 * interpolated between the thresholds we happened to evaluate.
 *
 * Trades are drawn as a second series, exactly as in the sensitivity chart: a
 * threshold that only looks better because it stopped trading is a false positive.
 * `effective_vote` is surfaced in the tooltip because it names the coalition the
 * threshold is actually waiting for, which is what the user is choosing between.
 *
 * Descriptive only. Nothing here recommends a threshold.
 */
export interface SweepPoint {
  vote_threshold: number
  effective_vote: number
  entries_taken: number
  entry_bars: number
  signalled_bars: number
  total_return: number
  max_drawdown: number
  sharpe: number | null
  win_rate: number | null
  number_of_trades: number
}

const props = defineProps<{ points: SweepPoint[]; height?: string }>()

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const RETURN_COLOR = '#4c8dff'
const TRADES_COLOR = '#d29922'

function pct(value: number | null | undefined): number | null {
  if (value == null) return null
  return Number((value * 100).toFixed(3))
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const points = props.points ?? []

  chart.setOption(
    {
      backgroundColor: 'transparent',
      grid: { left: 62, right: 56, top: 34, bottom: 46 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params]
          const index = list[0]?.dataIndex ?? 0
          const p = points[index]
          if (!p) return ''
          return [
            `阈值 ${fmtVote(p.vote_threshold)}`,
            `实际生效票数 ${fmtVote(p.effective_vote)}`,
            `总收益 ${fmtPct(p.total_return)}`,
            `最大回撤 ${fmtPct(p.max_drawdown)}`,
            `夏普 ${p.sharpe == null ? '—' : p.sharpe.toFixed(2)}`,
            `开仓 ${p.entries_taken} 次 / 投票通过 ${p.entry_bars} 根`,
            `有成员想入场 ${p.signalled_bars} 根`,
            `交易 ${p.number_of_trades} 笔`,
          ].join('<br/>')
        },
      },
      legend: { top: 0, textStyle: { color: '#8b949e' } },
      xAxis: {
        type: 'category',
        data: points.map((p) => fmtVote(p.vote_threshold)),
        name: '投票阈值',
        nameLocation: 'middle',
        nameGap: 28,
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      yAxis: [
        {
          type: 'value',
          name: '总收益 %',
          nameTextStyle: { color: '#8b949e' },
          scale: true,
          splitLine: { lineStyle: { color: '#1c2330' } },
          axisLine: { lineStyle: { color: '#2a3441' } },
        },
        {
          type: 'value',
          name: '交易数',
          nameTextStyle: { color: '#8b949e' },
          minInterval: 1,
          splitLine: { show: false },
          axisLine: { lineStyle: { color: '#2a3441' } },
        },
      ],
      series: [
        {
          name: '总收益 %',
          type: 'line',
          // The staircase is the finding: hold each value until the next threshold.
          step: 'end',
          symbolSize: 7,
          data: points.map((p) => pct(p.total_return)),
          lineStyle: { width: 2, color: RETURN_COLOR },
          itemStyle: { color: RETURN_COLOR },
          areaStyle: { color: 'rgba(76,141,255,0.08)' },
          markLine: {
            silent: true,
            symbol: 'none',
            label: { formatter: '0%', color: '#8b949e' },
            lineStyle: { color: '#8b949e', type: 'dashed', width: 1 },
            data: [{ yAxis: 0 }],
          },
        },
        {
          name: '交易数',
          type: 'line',
          yAxisIndex: 1,
          step: 'end',
          symbolSize: 5,
          data: points.map((p) => p.number_of_trades),
          lineStyle: { width: 1, type: 'dashed', color: TRADES_COLOR },
          itemStyle: { color: TRADES_COLOR },
        },
      ],
    },
    true,
  )
}

function fmtVote(value: number): string {
  return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

function fmtPct(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${(value * 100).toFixed(2)}%`
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})

watch(() => props.points, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart" :style="height ? { height } : undefined" />
</template>
