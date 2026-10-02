<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

export interface SeriesLine {
  name: string
  points: Array<{ ts: string; value: number | null }>
}

const props = defineProps<{ series: SeriesLine[]; height?: string }>()

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const COLORS = ['#4c8dff', '#3fb950', '#d29922', '#f85149', '#8b949e', '#bc8cff']

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(
    {
      backgroundColor: 'transparent',
      grid: { left: 56, right: 16, top: 30, bottom: 26 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { color: '#8b949e' }, type: 'scroll' },
      xAxis: { type: 'category', axisLine: { lineStyle: { color: '#2a3441' } } },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: '#1c2330' } },
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      series: props.series.map((s, i) => ({
        name: s.name,
        type: 'line',
        data: s.points.map((p) => (p.value == null ? null : Number(p.value.toFixed(2)))),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: COLORS[i % COLORS.length] },
        areaStyle: { color: 'rgba(76,141,255,0.08)' },
      })),
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

watch(() => props.series, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart" :style="height ? { height } : undefined" />
</template>
