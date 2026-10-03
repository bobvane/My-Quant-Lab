<script setup lang="ts">
// Every metric card can explain its own label (ADR-110): MetricHint renders nothing
// when the label is not a professional metric.
import MetricHint from '@/components/MetricHint.vue'

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
      <MetricHint :label="label" />
    </div>
    <div class="stat small" :class="tone ?? 'plain'">{{ value ?? 'N/A' }}</div>
    <div v-if="sub" class="muted">{{ sub }}</div>
  </div>
</template>
