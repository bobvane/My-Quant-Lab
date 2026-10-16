
/**
 * Scenario driver for the Lab request-sequence tests.
 *
 * The functions under test are NOT written here: lab_race_generated.mjs holds the
 * shipped bodies extracted from frontend/src/views/LabView.vue. This file only
 * chooses the order responses are delivered in, and records what the page would
 * show afterwards.
 */
import { tick, openRun, refreshRun, openExperimentById, closeExperiment,
         formalize, confirmDraft, handleCompileFailure,
         createExperiment, takeRunTicket } from './lab_race_generated.mjs'
import { snap, reset, settleAndFlush, pendingCount, loadingRun, pollFailures, ApiError,
         run, flush, currentDraftId, compileOutcome, compileOutcomeDraftId } from './lab_race_env.mjs'

const out = {}

// Scenarios run strictly one after another: they share one transport queue, and
// a reset() from a later scenario would otherwise wipe an earlier one in flight.

async function s1_reverseOrder() {
  reset()
  const pA = openRun(1)
  const pB = openRun(2)
  out.inflight_after_two_starts = pendingCount("run")
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  out.s1_after_B_returned = snap()
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  await pA; await pB
  out.s1_after_A_returned_late = snap()
}

async function s2_staleFailure() {
  reset()
  const pA = openRun(1)
  const pB = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, new ApiError("boom from A", 500), true)
  await pA; await pB
  out.s2_after_stale_failure = snap()
}

async function s3_currentFailure() {
  reset()
  const pA = openRun(1)
  const pB = openRun(2)
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  await settleAndFlush("run", -1, new ApiError("boom from B", 500), true)
  await pA; await pB
  out.s3_after_current_failure = snap()
}

async function s4_loadingState() {
  reset()
  const pA = openRun(1)
  const pB = openRun(2)
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  out.s4_loading_after_stale_finished = loadingRun.value
  await settleAndFlush("run", 0, { run_id: 2, status: "completed" })
  out.s4_loading_after_current_finished = loadingRun.value
  await pA; await pB
}

async function s5_sameIdTwice() {
  reset()
  const p1 = openRun(7)
  const p2 = openRun(7)
  await settleAndFlush("run", 1, { run_id: 7, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 7, status: "running" })
  await p1; await p2
  out.s5_same_id_repeat = snap()
}

async function s6_refreshOvertaken() {
  reset()
  const p1 = openRun(1)
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  const pRefresh = refreshRun()
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  await pRefresh; await pSwitch; await p1
  out.s6_after_refresh_then_switch = snap()
}

async function s7_pollTickOvertaken() {
  reset()
  const p1 = openRun(1)
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  const pTick = tick()
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 1, status: "completed" })
  await pTick; await pSwitch; await p1
  out.s7_after_stale_poll_tick = snap()
}

async function s8_experimentReverseOrder() {
  reset()
  const pA = openExperimentById(10)
  const pB = openExperimentById(20)
  await settleAndFlush("exp", 1, { id: 20, name: "B" })
  out.s8_after_B_returned = snap()
  await settleAndFlush("exp", 0, { id: 10, name: "A" })
  await pA; await pB
  out.s8_after_A_returned_late = snap()
}

async function s9_staleExperiment404() {
  reset()
  const pA = openExperimentById(10)
  const pB = openExperimentById(20)
  await settleAndFlush("exp", 1, { id: 20, name: "B" })
  await settleAndFlush("exp", 0, new ApiError("gone", 404), true)
  await pA; await pB
  out.s9_after_stale_404 = snap()
}

async function s10_closeInvalidates() {
  reset()
  const pB = openExperimentById(20)
  closeExperiment()
  await settleAndFlush("exp", 0, { id: 20, name: "B" })
  await pB
  out.s10_after_close_then_late = snap()
}

async function s11_stalePollFailure() {
  reset()
  const p1 = openRun(1)
  await settleAndFlush("run", 0, { run_id: 1, status: "running" })
  const pTick = tick()
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, new ApiError("poll died", 500), true)
  await pTick; await pSwitch; await p1
  out.s11_pollFailures_after_stale_poll_error = pollFailures.value
}

// --------------------------------------------------------------------- //
// The async entry points the first round left unguarded. Each one used to do
// `applyRun(await ...)` with no ticket, so a late response could put an older
// research back on screen after the user had already switched away.
// --------------------------------------------------------------------- //

// formalize(): user is on run 1, rewrites the draft, then switches to run 2
// before the re-read comes back.
async function s12_formalizeOvertaken() {
  reset()
  run.value = { run_id: 1, status: 'running' }
  const pFormalize = formalize()
  await flush()                       // the POST resolves, the re-read is queued
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 1, status: "completed" })
  await pFormalize; await pSwitch
  out.s12_after_formalize_answered_late = snap()
}

// Same shape, but the 422 branch: the re-read happens inside the catch, so it
// must still be tied to the same ticket rather than starting a fresh one.
async function s13_formalize422Overtaken() {
  reset()
  run.value = { run_id: 1, status: 'running' }
  const pFormalize = formalize()
  await flush()
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 1, status: "completed" })
  await pFormalize; await pSwitch
  out.s13_after_formalize422_answered_late = snap()
}

// confirmDraft(): the chained POST + re-read must not be self-cancelled, but a
// switch mid-flight must still win.
async function s14_confirmDraftOvertaken() {
  reset()
  run.value = { run_id: 1, status: 'running', draft: { draft_id: 11 } }
  const pConfirm = confirmDraft("confirmed")
  await flush()
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 1, status: "completed" })
  await pConfirm; await pSwitch
  out.s14_after_confirm_answered_late = snap()
}

// The same chained operation with nothing in between: the re-read must still be
// applied, i.e. the ticket is not so eager that it invalidates its own steps.
async function s15_confirmDraftUninterrupted() {
  reset()
  run.value = { run_id: 5, status: 'running', draft: { draft_id: 11 } }
  const pConfirm = confirmDraft("confirmed")
  await flush()
  await settleAndFlush("run", 0, { run_id: 5, status: "completed" })
  await pConfirm
  out.s15_confirm_uninterrupted = snap()
}

// handleCompileFailure(): the 422-with-body path must not be gated by the
// ticket, because it writes a compile report rather than the shown research.
async function s16_compileReportStillShown() {
  reset()
  const err = new ApiError("编译失败", 422)
  err.body = { result: "ERROR", report: { a: 1 } }
  currentDraftId.value = 77
  await handleCompileFailure(err, 0)
  out.s16_compile_report = compileOutcome.value
  out.s16_compile_report_draft = compileOutcomeDraftId.value
}

// A stale compile failure must not re-read the old research onto the page.
// The compile started while run 1 was shown, so it holds ticket 1; the user
// then opens run 2 (ticket 2). The late answer for run 1 must be dropped.
async function s17_staleCompileReReadDropped() {
  reset()
  run.value = { run_id: 1, status: 'running' }
  const err = new ApiError("还没确认", 409)
  err.code = "draft_not_confirmed"
  const compileTicket = takeRunTicket()   // what compile() itself would take
  const pHandle = handleCompileFailure(err, compileTicket)
  await flush()                          // its re-read for run 1 is in flight
  const pSwitch = openRun(2)
  await settleAndFlush("run", 1, { run_id: 2, status: "completed" })
  await settleAndFlush("run", 0, { run_id: 1, status: "completed" })
  await pHandle; await pSwitch
  out.s17_after_stale_compile_reread = snap()
}
// createExperiment(): the engine really runs, so this is the slowest call on
// the page. Opening another experiment while it is in flight must win, and the
// late create must not drag the user back to the record it just made.
async function s18_createExperimentOvertaken() {
  reset()
  const pCreate = createExperiment()
  await flush()
  const pOpen = openExperimentById(20)
  await settleAndFlush("exp", -1, { id: 20, status: "completed" })
  out.s18_after_experiment_20_opens = snap()
  await settleAndFlush("newexp", 0, { id: 30, status: "completed" })
  await pCreate; await pOpen
  out.s18_after_create_answered_late = snap()
}

// The same operation with nothing in between still has to land.
async function s19_createExperimentUninterrupted() {
  reset()
  const pCreate = createExperiment()
  await flush()
  await settleAndFlush("newexp", 0, { id: 30, status: "completed" })
  await pCreate
  out.s19_create_uninterrupted = snap()
}
async function main() {
  await s1_reverseOrder()
  await s2_staleFailure()
  await s3_currentFailure()
  await s4_loadingState()
  await s5_sameIdTwice()
  await s6_refreshOvertaken()
  await s7_pollTickOvertaken()
  await s8_experimentReverseOrder()
  await s9_staleExperiment404()
  await s10_closeInvalidates()
  await s11_stalePollFailure()
  await s12_formalizeOvertaken()
  await s13_formalize422Overtaken()
  await s14_confirmDraftOvertaken()
  await s15_confirmDraftUninterrupted()
  await s16_compileReportStillShown()
  await s17_staleCompileReReadDropped()
  await s18_createExperimentOvertaken()
  await s19_createExperimentUninterrupted()
  console.log(JSON.stringify(out, null, 2))
}

main().catch(e => { console.error('SCENARIO FAILED:', e.message); process.exit(3) })
