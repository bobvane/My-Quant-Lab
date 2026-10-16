/**
 * Shared environment for the Lab request-sequence tests.
 *
 * It holds the reactive state and the collaborators the extracted LabView bodies
 * close over, plus a transport whose response order the test controls. Both the
 * generated module (the shipped function bodies) and the scenario harness import
 * from here, so they can never end up with two different copies of the queue.
 *
 * 'vue' is an existing runtime dependency; no test framework is used or added.
 */
import { ref } from 'vue'

export const run = ref(null)
export const openExperiment = ref(null)
export const openExperimentId = ref(null)
export const loadingRun = ref(false)
export const loadingExperiment = ref(false)
export const error = ref('')
export const experimentDetailError = ref('')
export const notice = ref('')
export const pollFailures = ref(0)
export const pollStartedAt = ref(null)
export const elapsedSeconds = ref(0)
export const url = ref(null)

// Experiment state that createExperiment() writes.
export const creatingExperiment = ref(false)
export const experimentBlockedReason = ref('')
export const experimentError = ref('')
export const experimentNotice = ref('')
// The form is not what these scenarios are about; a payload that passes the
// guard is enough to let the function reach its await.
export function buildExperimentPayload() { return { question: 'x' } }
export function handleExperimentFailure(e) { experimentError.value = String(e && e.message) }

// State the extracted action functions (formalize / confirmDraft /
// handleCompileFailure) close over.
export const formalizing = ref(false)
export const confirming = ref(false)
export const confirmationNote = ref('')
export const compileOutcome = ref(null)
export const compileOutcomeDraftId = ref(null)
export const compileError = ref('')
export const compileDetail = ref('')
export const currentDraftId = ref(null)
export const COMPILE_ERROR_REASONS = {
  draft_not_confirmed: '这份草案还没人工确认，先确认再编译。',
  draft_already_compiled: '这份草案已经编译成策略版本了。',
}
export function decisionLabel(d) { return d === 'confirmed' ? '已确认' : '已放弃' }
export function loadQuestions() {}

export function rememberOpenRun(id) { url.value = id }
export function rememberOpenExperiment(id) { url.value = id }
export function applyRun(fresh) { run.value = fresh; rememberOpenRun(fresh.run_id) }
export function applyExperiment(d) {
  openExperiment.value = d
  openExperimentId.value = d.id
  rememberOpenExperiment(d.id)
}
export function handleFailure(e) { error.value = (e && e.message) || String(e) }
export function stopPolling() {}
export function resetNotices() { notice.value = '' }
export function loadRuns() {}
export function loadExperiments() {}
export function isLiveStatus(s) { return s === 'running' || s === 'queued' || s === 'pending' }
export const POLL_FAILURE_LIMIT = 3
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status }
}

// In-flight requests, in the order they were started.
export const pending = []
export function aiResearchRun(id) {
  return new Promise((resolve, reject) => pending.push({ kind: 'run', id, resolve, reject }))
}
export function experiment(id) {
  return new Promise((resolve, reject) => pending.push({ kind: 'exp', id, resolve, reject }))
}
// Every other endpoint the extracted bodies call. The scenarios only assert on
// the detail, the URL, the error and the loading flags, so these resolve
// immediately; what matters is that they do not throw, which would otherwise
// turn a sequencing question into an unrelated failure.
export function unmodelled() { return Promise.resolve(null) }
// createExperiment is slow (the engine really runs), which is exactly why it
// can be overtaken -- so it has to be steerable like the reads are.
export function createExperiment(payload) {
  return new Promise((resolve, reject) => pending.push({ kind: 'newexp', id: null, resolve, reject }))
}
export const __tickApi = { aiResearchRun, experiment, createExperiment, ApiError, unmodelled }

// Settles the in-flight request at `index` (oldest first) and drops it from the
// queue, so the next index keeps meaning what the scenario expects.
export function settle(kind, index, payload, isError = false) {
  const list = pending.filter(p => p.kind === kind)
  const p = index === -1 ? list[list.length - 1] : list[index]
  if (!p) throw new Error('no pending ' + kind)
  pending.splice(pending.indexOf(p), 1)
  if (isError) p.reject(payload); else p.resolve(payload)
}
export function pendingCount(kind) { return pending.filter(p => p.kind === kind).length }
export const flush = () => new Promise(r => setImmediate(r))
export async function settleAndFlush(kind, index, payload, isError = false) {
  settle(kind, index, payload, isError)
  await flush()
  await flush()
}

export function snap() {
  return {
    runId: run.value ? run.value.run_id : null,
    url: url.value,
    expId: openExperimentId.value,
    loadingRun: loadingRun.value,
    loadingExperiment: loadingExperiment.value,
    error: error.value,
    expError: experimentDetailError.value,
    notice: notice.value,
    compileError: compileError.value,
    pollFailures: pollFailures.value,
  }
}
export function reset() {
  run.value = null
  openExperiment.value = null
  openExperimentId.value = null
  loadingRun.value = false
  loadingExperiment.value = false
  error.value = ''
  experimentDetailError.value = ''
  notice.value = ''
  compileError.value = ''
  compileDetail.value = ''
  compileOutcome.value = null
  compileOutcomeDraftId.value = null
  currentDraftId.value = null
  formalizing.value = false
  confirming.value = false
  confirmationNote.value = ''
  creatingExperiment.value = false
  experimentError.value = ''
  experimentNotice.value = ''
  pollFailures.value = 0
  pollStartedAt.value = null
  elapsedSeconds.value = 0
  url.value = null
  pending.length = 0
}

export const ENV_NAMES = [
  'run', 'openExperiment', 'openExperimentId', 'loadingRun', 'loadingExperiment',
  'error', 'experimentDetailError', 'notice', 'pollFailures', 'pollStartedAt',
  'elapsedSeconds', 'url',
  'rememberOpenRun', 'rememberOpenExperiment', 'applyRun', 'applyExperiment',
  'handleFailure', 'stopPolling', 'resetNotices', 'loadRuns',
  'isLiveStatus', 'POLL_FAILURE_LIMIT', '__tickApi',
  'creatingExperiment', 'experimentBlockedReason', 'buildExperimentPayload',
  'experimentError', 'experimentNotice',
  'handleExperimentFailure',
  'formalizing', 'confirming', 'confirmationNote', 'compileOutcome',
  'compileOutcomeDraftId', 'compileError', 'compileDetail', 'currentDraftId',
  'COMPILE_ERROR_REASONS', 'decisionLabel', 'loadQuestions',
]
