<script setup lang="ts">
// Answers the four questions in place: what it is / how it is computed / why look at it /
// what it does not tell you. Tooltip on hover, and the full note expands on click
// (ADR-110, docs/13_UI_UX.md section 7).
import { computed, ref } from 'vue'

import { metricNote } from '@/metrics'

const props = defineProps<{ label: string }>()

const note = computed(() => metricNote(props.label))
const open = ref(false)
const questions = [
  { key: 'what', title: '是什么' },
  { key: 'how', title: '怎么算' },
  { key: 'why', title: '为什么看它' },
  { key: 'watch', title: '注意什么' },
] as const
</script>

<template>
  <span v-if="note" class="metric-hint-wrap">
    <button
      type="button"
      class="metric-hint"
      :aria-expanded="open"
      :title="`${label}：${note.what}`"
      @click="open = !open"
    >
      四问
    </button>
    <dl v-if="open" class="metric-note">
      <template v-for="question in questions" :key="question.key">
        <dt>{{ question.title }}</dt>
        <dd>{{ note[question.key] }}</dd>
      </template>
    </dl>
  </span>
</template>
