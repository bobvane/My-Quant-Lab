<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

/**
 * Sensitivity surface for `POST /research/sensitivity` (docs/21).
 *
 * One axis  -> a line/area over the swept values, so the trend is readable.
 * Two axes  -> a heatmap so sign flips across the neighbourhood are visible at a
 *              glance (a strategy that only works at one exact setting shows up as
 *              a single bright cell surrounded by dark ones).
 *
 * Trades are drawn as a second series where possible: a parameter that only looks
 * good because it stopped trading entirely is a classic false positive.
 */
export interface SensitivityPoint {
  parameters: Record<string, number | string>
  objective: number | null
  metrics: Record<string, number | null>
  result_hash?: string
}

const props = defineProps<{
  points: SensitivityPoint[]
  axes: Record<string, Array<number | string>>
  metric: string
  height?: string
}>()

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const AXIS_COLORS = ['#4c8dff', '#3fb950', '#d29922']

function axisNames(): string[] {
  return Object.keys(props.axes ?? {})
}

/** Objectives laid out on the 2-axis grid; nulls become '-' so ECharts skips them. */
function heatmapData(xName: string, yName: string) {
  const xs = props.axes[xName] ?? []
  const ys = props.axes[yName] ?? []
  const out: Array<[number, number, number | string]> = []
  for (const p of props.points) {
    const xi = xs.indexOf(p.parameters[xName])
    const yi = ys.indexOf(p.parameters[yName])
    if (xi < 0 || yi < 0) continue
    out.push([xi, yi, p.objective == null ? '-' : Number(p.objective.toFixed(4))])
  }
  return out
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const axes = axisNames()
  const base = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' as const },
    grid: { left: 60, right: 24, top: axes.length > 1 ? 48 : 34, bottom: 46 },
  }

  if (axes.length >= 2) {
    const [xName, yName] = axes
    const values = props.points
      .map((p) => p.objective)
      .filter((v): v is number => v != null)
    const min = values.length ? Math.min(...values) : -1
    const max = values.length ? Math.max(...values) : 1
    chart.setOption(
      {
        ...base,
        tooltip: {
          position: 'top',
          formatter: (params: any) => {
            const [xi, yi, v] = params.data as [number, number, number | string]
            return `${xName}=${props.axes[xName][xi]}<br/>${yName}=${props.axes[yName][yi]}<br/>${props.metric}=${v}`
          },
        },
        xAxis: {
          type: 'category',
          data: props.axes[xName],
          name: xName,
          nameLocation: 'middle',
          nameGap: 28,
          axisLine: { lineStyle: { color: '#2a3441' } },
        },
        yAxis: {
          type: 'category',
          data: props.axes[yName],
          name: yName,
          nameLocation: 'middle',
          nameGap: 44,
          axisLine: { lineStyle: { color: '#2a3441' } },
        },
        visualMap: {
          min,
          max,
          calculable: true,
          orient: 'horizontal',
          left: 'center',
          top: 4,
          textStyle: { color: '#8b949e' },
          inRange: { color: ['#f85149', '#d29922', '#3fb950'] },
        },
        series: [
          {
            name: props.metric,
            type: 'heatmap',
            data: heatmapData(xName, yName),
            label: { show: true, color: '#e6edf3', fontSize: 11 },
            emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.5)' } },
          },
        ],
      },
      true,
    )
    return
  }

  // Single axis (or a degenerate grid): a line over the swept values.
  const xName = axes[0] ?? 'point'
  const categories = props.points.map((p, i) =>
    xName === 'point' ? String(i) : String(p.parameters[xName]),
  )
  chart.setOption(
    {
      ...base,
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { color: '#8b949e' } },
      xAxis: {
        type: 'category',
        data: categories,
        name: xName,
        nameLocation: 'middle',
        nameGap: 28,
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: '#1c2330' } },
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      series: [
        {
          name: props.metric,
          type: 'line',
          smooth: true,
          symbolSize: 7,
          data: props.points.map((p) => (p.objective == null ? null : Number(p.objective.toFixed(4)))),
          lineStyle: { width: 2, color: AXIS_COLORS[0] },
          itemStyle: { color: AXIS_COLORS[0] },
          areaStyle: { color: 'rgba(76,141,255,0.08)' },
          connectNulls: false,
        },
        {
          name: '交易数',
          type: 'line',
          yAxisIndex: 0,
          smooth: false,
          symbolSize: 5,
          data: props.points.map((p) => p.metrics?.number_of_trades ?? null),
          lineStyle: { width: 1, type: 'dashed', color: AXIS_COLORS[2] },
          itemStyle: { color: AXIS_COLORS[2] },
        },
      ],
    },
    true,
  )
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

watch(() => [props.points, props.axes, props.metric], render, { deep: true })
</script>

<template>
  <div ref="el" class="chart" :style="height ? { height } : undefined" />
</template>
