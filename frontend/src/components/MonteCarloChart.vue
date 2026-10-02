<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

/**
 * Monte Carlo fan chart (docs/22).
 *
 * Trade index on x (a resampled path has no dates — the trade *order* is what is
 * being reshuffled), equity on y. The individual sample paths are drawn faintly and
 * the p5/p50/p95 envelope on top, so the eye reads the spread rather than one line.
 *
 * The chart deliberately does not extrapolate or label a "target": it shows the
 * dispersion of resampled history.
 */
const props = defineProps<{
  paths: number[][]
  initialCapital: number
  height?: string
}>()

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function percentilesAt(paths: number[][], index: number): [number, number, number] | null {
  const values = paths
    .map((p) => p[index])
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    .sort((a, b) => a - b)
  if (!values.length) return null
  const at = (q: number) => values[Math.min(values.length - 1, Math.max(0, Math.round(q * (values.length - 1))))]
  return [at(0.05), at(0.5), at(0.95)]
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const paths = props.paths ?? []
  if (!paths.length) {
    chart.clear()
    return
  }

  const length = paths[0].length
  const x = Array.from({ length }, (_, i) => i)

  const p5: Array<number | null> = []
  const p50: Array<number | null> = []
  const p95: Array<number | null> = []
  for (let i = 0; i < length; i++) {
    const band = percentilesAt(paths, i)
    p5.push(band ? Number(band[0].toFixed(2)) : null)
    p50.push(band ? Number(band[1].toFixed(2)) : null)
    p95.push(band ? Number(band[2].toFixed(2)) : null)
  }

  // Cap the faint overlay so the chart stays readable and light to render.
  const shown = paths.slice(0, 40)

  chart.setOption(
    {
      backgroundColor: 'transparent',
      grid: { left: 64, right: 20, top: 34, bottom: 40 },
      tooltip: { trigger: 'axis' },
      legend: {
        top: 0,
        textStyle: { color: '#8b949e' },
        data: ['样本路径', '中位数 p50', 'p5 / p95'],
      },
      xAxis: {
        type: 'category',
        data: x,
        name: '交易序号',
        nameLocation: 'middle',
        nameGap: 26,
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: '权益',
        splitLine: { lineStyle: { color: '#1c2330' } },
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      series: [
        ...shown.map((path, idx) => ({
          name: '样本路径',
          type: 'line' as const,
          data: path.map((v) => Number(v.toFixed(2))),
          showSymbol: false,
          silent: true,
          lineStyle: { width: 1, color: 'rgba(139,148,158,0.28)' },
          // Only the first entry carries the legend label.
          legendHoverLink: false,
          ...(idx === 0 ? {} : { tooltip: { show: false } }),
        })),
        {
          name: '中位数 p50',
          type: 'line' as const,
          data: p50,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 2.5, color: '#4c8dff' },
          itemStyle: { color: '#4c8dff' },
          z: 10,
        },
        {
          name: 'p5 / p95',
          type: 'line' as const,
          data: p95,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.4, type: 'dashed' as const, color: '#3fb950' },
          itemStyle: { color: '#3fb950' },
          z: 9,
        },
        {
          name: 'p95',
          type: 'line' as const,
          data: p5,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.4, type: 'dashed' as const, color: '#f85149' },
          itemStyle: { color: '#f85149' },
          z: 9,
          tooltip: { show: false },
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

watch(() => props.paths, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart" :style="height ? { height } : undefined" />
</template>
