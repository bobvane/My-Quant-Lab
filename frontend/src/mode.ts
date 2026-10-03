// 普通模式 / 高级模式（ADR-126）.
//
// The product audit found the same thing on every page: a professional research
// backend and a household-facing product were mixed together, so a reader who
// does not know what a feature version is had to walk past one to reach the
// number they came for. The fix is a display layer, not a second product —
// ``basic`` is the default and hides engineering readouts, ``advanced`` shows
// everything the engine reports. Nothing is deleted: every professional
// capability stays exactly where it is, one click away.

import { computed, ref } from 'vue'

export type UiMode = 'basic' | 'advanced'

const STORAGE_KEY = 'mql-mode'

function stored(): UiMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'advanced' ? 'advanced' : 'basic'
  } catch {
    // Private mode or a blocked storage bucket: the plain interface is the safe
    // default, because it is the one that hides rather than reveals.
    return 'basic'
  }
}

/** The one place the current display mode lives. */
export const mode = ref<UiMode>(stored())

/** True only while the reader has asked for the engineering view. */
export const isAdvanced = computed(() => mode.value === 'advanced')

/**
 * Switch modes and remember the answer.
 *
 * The mode is also written to `document.documentElement.dataset.mode` so the
 * stylesheet can react (a few blocks only need tighter spacing once the
 * engineering rows are gone). A reload re-reads the stored choice, so the
 * attribute and the ref never disagree.
 */
export function setMode(value: UiMode): void {
  mode.value = value
  document.documentElement.dataset.mode = value
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // Remembering the choice is a convenience, not a requirement.
  }
}

/** Called once on boot so the attribute matches the ref before the first paint. */
export function initMode(): void {
  document.documentElement.dataset.mode = mode.value
}
