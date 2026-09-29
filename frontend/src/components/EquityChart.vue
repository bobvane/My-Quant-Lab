<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ points: Array<{ timestamp: string; equity: number }>; height?: string }>()

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const xs = props.points.map((p) => new Date(p.timestamp).toLocaleDateString())
  const ys = props.points.map((p) => Number(p.equity.toFixed(2)))
  chart.setOption(
    {
      backgroundColor: 'transparent',
      grid: { left: 56, right: 16, top: 18, bottom: 26 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: xs, axisLine: { lineStyle: { color: '#2a3441' } } },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: '#1c2330' } },
        axisLine: { lineStyle: { color: '#2a3441' } },
      },
      series: [
        {
          type: 'line',
          data: ys,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: '#4c8dff' },
          areaStyle: { color: 'rgba(76,141,255,0.12)' },
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

watch(() => props.points, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart" :style="height ? { height } : undefined" />
</template>
