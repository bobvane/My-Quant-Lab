<script setup lang="ts">
defineProps<{
  label: string
  value: string | number | null | undefined
  sub?: string
  tone?: 'pos' | 'neg' | 'plain'
}>()
</script>

<template>
  <div class="card">
    <h3>{{ label }}</h3>
    <div class="stat small" :class="tone ?? 'plain'">{{ value ?? 'N/A' }}</div>
    <div v-if="sub" class="muted">{{ sub }}</div>
  </div>
</template>
