<script setup lang="ts">
// Every metric card explains its own label (ADR-110, ADR-127): the one-line note is
// visible without being asked for, the professional term gets its plain meaning next
// to it, and the four questions stay one click away. MetricHint renders nothing when
// the label is not a professional metric.
import MetricHint from '@/components/MetricHint.vue'
import { metricPlain } from '@/metrics'
import { termAlias } from '@/wording'

defineProps<{
  label: string
  value: string | number | null | undefined
  sub?: string
  tone?: 'pos' | 'neg' | 'plain'
}>()
</script>

<template>
  <div class="card">
    <div class="metric-head">
      <h3>{{ label }}</h3>
      <span v-if="termAlias(label)" class="metric-alias">（{{ termAlias(label) }}）</span>
      <MetricHint :label="label" />
    </div>
    <div class="stat small" :class="tone ?? 'plain'">{{ value ?? 'N/A' }}</div>
    <p v-if="metricPlain(label)" class="metric-plain">{{ metricPlain(label) }}</p>
    <div v-if="sub" class="muted">{{ sub }}</div>
  </div>
</template>
