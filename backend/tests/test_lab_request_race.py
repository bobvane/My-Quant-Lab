"""Runtime behaviour tests for the Lab request-sequence guard (ADR-221).

These are NOT source-text assertions. Each run:

1. extracts the shipped tick / openRun / refreshRun / openExperimentById /
   closeExperiment bodies out of frontend/src/views/LabView.vue
   (see lab_race_extract),
2. runs them under Node against a transport whose response order the test
   chooses, so "A was asked first but answers last" is reproducible,
3. asserts on what the page would show: the detail, the address bar, the error
   line and the loading flags.

Without the guard, a request that was started first but returns last silently
overwrites the newer one, and applyRun -> rememberOpenRun rewinds the address bar
too, so the page contradicts the URL it is on.

The repo ships no JS test runner (no vitest/jsdom in frontend/package.json), so
the scenarios live in a plain .mjs harness driven from here, using only Node and
the vue package that already is a runtime dependency. No dependency is added.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import lab_race_extract
import pytest

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = lab_race_extract.REPO_ROOT / "frontend"
HARNESS = FRONTEND / "lab_race_harness.mjs"
GENERATED = FRONTEND / "lab_race_generated.mjs"

node = shutil.which("node")

NODE_DIR = FRONTEND / "node_modules"


def _resolves_vue() -> bool:
    """Can Node actually import `vue` from frontend/ ?

    Checking node_modules/ alone is not enough: a partial or interrupted install
    leaves the directory present but unusable, and the failure would then look
    like a broken test instead of a missing environment.
    """
    if node is None or not NODE_DIR.exists():
        return False
    probe = FRONTEND / "lab_race_probe.mjs"
    probe.write_text("import 'vue'\n", encoding="utf-8")
    try:
        done = subprocess.run(
            [node, str(probe)],
            cwd=str(FRONTEND),
            capture_output=True,
            timeout=120,
        )
        return done.returncode == 0
    finally:
        probe.unlink(missing_ok=True)


HAVE_NODE = node is not None
HAVE_VUE = _resolves_vue()

NO_NODE = "node is required to run the Lab request-sequence scenarios"
NO_VUE = (
    "frontend/node_modules is missing: run npm ci in frontend/ "
    "(this test adds no dependency of its own)"
)
#: Say which of the two is actually absent, rather than blaming both.
SKIP_REASON = NO_NODE if not HAVE_NODE else (NO_VUE if not HAVE_VUE else "")

#: Node itself is enough for the pure-extraction checks; the behaviour
#: scenarios additionally need `vue` to resolve from frontend/.
needs_node = pytest.mark.skipif(not HAVE_NODE, reason=NO_NODE)
needs_vue = pytest.mark.skipif(not HAVE_VUE, reason=SKIP_REASON or NO_VUE)

#: Import list of the shared environment (frontend/lab_race_env.mjs).
ENV_IMPORT = (
    "import { run, loadingRun, error, rememberOpenRun, applyRun, handleFailure, "
    "stopPolling, resetNotices, __tickApi, snap, reset, settleAndFlush } "
    "from './lab_race_env.mjs'\n"
)


def _run_variant(body: str) -> dict:
    """Run one openRun-shaped function against the shared env and report the page.

    Used to run the pre-fix body verbatim: same transport, same ordering, so
    the difference in outcome is the guard and nothing else.
    """
    driver = (
        ENV_IMPORT + body + "async function main() {\n"
        "  reset()\n"
        "  const pA = openRun(1)\n"
        "  const pB = openRun(2)\n"
        "  await settleAndFlush('run', 1, { run_id: 2, status: 'completed' })\n"
        "  await settleAndFlush('run', 0, { run_id: 1, status: 'running' })\n"
        "  await pA; await pB\n"
        "  console.log(JSON.stringify(snap()))\n"
        "}\n"
        "main()\n"
    )
    path = FRONTEND / "lab_race_variant.mjs"
    path.write_text(driver, encoding="utf-8")
    try:
        proc = subprocess.run(
            [node, str(path)],
            cwd=str(FRONTEND),
            env=dict(os.environ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        assert proc.returncode == 0, f"variant failed:\n{proc.stdout}\n{proc.stderr}"
        return json.loads(proc.stdout)
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def seen():
    """Run every scenario once against the extracted source."""
    # A fixture cannot carry a skipif mark, so the same condition is raised here.
    if SKIP_REASON:
        pytest.skip(SKIP_REASON)

    GENERATED.write_text(lab_race_extract.build_module(), encoding="utf-8")

    proc = subprocess.run(
        [node, str(HARNESS)],
        cwd=str(FRONTEND),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_a_late_first_request_never_overwrites_the_newer_one(seen):
    """B asked second, answers first, A answers last: B must stay on screen."""
    assert seen["inflight_after_two_starts"] == 2
    after_b = seen["s1_after_B_returned"]
    assert after_b["runId"] == 2
    assert after_b["url"] == 2

    after_a = seen["s1_after_A_returned_late"]
    assert after_a["runId"] == 2, "the late first request took over the page"
    assert after_a["url"] == 2, "the address bar was rewound to the stale record"
    assert after_a["error"] == ""


def test_a_stale_failure_does_not_speak_over_a_good_newer_result(seen):
    """A fails after B already succeeded: B keeps the screen and the error box."""
    state = seen["s2_after_stale_failure"]
    assert state["runId"] == 2
    assert state["url"] == 2
    assert state["error"] == "", "a stale error overwrote the current result"


def test_the_current_failure_is_reported_and_no_stale_result_appears(seen):
    """B is the live request and B fails: show B error, never A result."""
    state = seen["s3_after_current_failure"]
    assert state["error"] == "boom from B"
    assert state["runId"] is None, "A late success was shown after B failed"
    assert state["url"] is None


def test_a_stale_request_ending_first_does_not_clear_the_loading_flag(seen):
    """Loading belongs to the live request, so A finishing first must not stop it."""
    assert seen["s4_loading_after_stale_finished"] is True
    assert seen["s4_loading_after_current_finished"] is False


def test_the_same_id_twice_is_decided_by_order_not_by_the_id(seen):
    """Two opens of one id: the later request wins even though the id matches."""
    state = seen["s5_same_id_repeat"]
    assert state["runId"] == 7
    assert state["url"] == 7


def test_a_refresh_overtaken_by_a_switch_cannot_pull_the_page_back(seen):
    """Refreshing run 1, then switching to run 2: run 2 stays put."""
    state = seen["s6_after_refresh_then_switch"]
    assert state["runId"] == 2
    assert state["url"] == 2


def test_a_stale_polling_tick_cannot_resurrect_the_previous_run(seen):
    """A poll for run 1 answering after the switch must not undo it."""
    state = seen["s7_after_stale_poll_tick"]
    assert state["runId"] == 2
    assert state["url"] == 2


def test_a_stale_poll_failure_is_not_charged_to_the_new_record(seen):
    """A dead poll for the old run must not drive the new run to a hard stop."""
    assert seen["s11_pollFailures_after_stale_poll_error"] == 0


def test_experiment_reverse_order_keeps_the_later_record(seen):
    """Same rule for experiments: B asked second, A answers last, B stays."""
    assert seen["s8_after_B_returned"]["expId"] == 20
    late = seen["s8_after_A_returned_late"]
    assert late["expId"] == 20
    assert late["url"] == 20


def test_a_stale_404_does_not_wipe_the_experiment_now_on_screen(seen):
    """A 404 about the old experiment must not clear the current one."""
    state = seen["s9_after_stale_404"]
    assert state["expId"] == 20
    assert state["url"] == 20
    assert state["expError"] == "", "a stale 404 raised an error for the current view"


def test_closing_invalidates_the_read_in_flight_and_clears_loading(seen):
    """Closing while a read is in flight: nothing comes back, no stuck spinner."""
    state = seen["s10_after_close_then_late"]
    assert state["expId"] is None, "a closed experiment was reopened by its own response"
    assert state["url"] is None, "a closed experiment put its id back in the address bar"
    assert state["loadingExperiment"] is False, "the spinner was left running forever"


def test_the_guard_is_actually_in_the_source_under_test():
    """The extracted module must contain the shipped guard, not a stale copy.

    This guards the harness itself: if extraction silently produced empty bodies,
    every assertion above would pass without running any shipped code.
    """
    module = lab_race_extract.build_module()
    assert "runRequestSeq" in module
    assert "experimentRequestSeq" in module
    # tick(), openRun() and refreshRun() each drop a stale response; openRun()
    # and refreshRun() also refuse a stale catch, and tick() a stale failure.
    assert module.count("if (seq !== runRequestSeq) return") >= 4
    # the finally of openRun/refreshRun (run counter) and of
    # openExperimentById (experiment counter) each gate their loading flag.
    assert module.count("if (seq === runRequestSeq)") == 2
    assert module.count("if (seq === experimentRequestSeq)") == 1
    # closeExperiment must invalidate the in-flight read too.
    assert "experimentRequestSeq += 1" in module


def test_every_page_writing_call_site_is_ticketed():
    """No async site may write the shown run without taking a ticket first.

    The behaviour scenarios above can only drive the five functions the harness
    extracts. This one covers the whole component: an applyRun(...) that is not
    preceded by a runRequestSeq guard can still put an older research back on
    screen (and in the address bar) after the user switched away.
    """
    source = lab_race_extract.LAB.read_text(encoding="utf-8")
    guarded = 0
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("applyRun("):
            continue
        guarded += 1
        window = source[: source.index(line)]
        recent = window[-1200:] if len(window) > 1200 else window
        assert "runRequestSeq" in recent, (
            f"applyRun at {stripped!r} has no recent runRequestSeq guard"
        )
    # 13 = 5 extracted functions (tick/openRun/refreshRun) plus the 8 async
    # entry points the harness cannot drive (submit, formalize x2,
    # confirmDraft, submitAnswers, two answer-recovery reads, compile).
    assert guarded >= 13, f"expected every applyRun site, found only {guarded}"


def test_every_experiment_writing_call_site_is_ticketed():
    """Same coverage for the experiment side (ADR-221)."""
    source = lab_race_extract.LAB.read_text(encoding="utf-8")
    guarded = 0
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("applyExperiment("):
            continue
        guarded += 1
        window = source[: source.index(line)]
        recent = window[-1200:] if len(window) > 1200 else window
        assert "experimentRequestSeq" in recent, (
            f"applyExperiment at {stripped!r} has no recent guard"
        )
    # openExperimentById plus createExperiment.
    assert guarded >= 2, f"expected every applyExperiment site, found {guarded}"


def test_a_late_created_experiment_does_not_yank_the_user_back(seen):
    """Creating an experiment is the slowest call on the page."""
    state = seen["s18_after_create_answered_late"]
    assert state["expId"] == 20, "a stale create dragged the user off the record they opened"
    assert state["url"] == 20


def test_an_uninterrupted_create_still_lands(seen):
    """The guard must not cost the normal path its result."""
    state = seen["s19_create_uninterrupted"]
    assert state["expId"] == 30
    assert state["url"] == 30


def test_type_stripping_cannot_change_what_the_code_does():
    """The TS strip must remove annotations only, never rewrite behaviour.

    A greedy cast rule once turned ``foo(bar as Baz)`` into ``foobar`` -- a
    silent behaviour change that would have made the scenarios prove nothing.
    """
    cases = [
        ("foo(bar as Baz)", "foo(bar)"),
        ("x = y as Error", "x = y"),
        ("const a = b as unknown[]", "const a = b"),
        (
            "report: (e.body.report ?? null) as Record<string, any> | null,",
            "report: (e.body.report ?? null),",
        ),
        ("return (a && b) as boolean", "return (a && b)"),
    ]
    for source, expected in cases:
        stripped = lab_race_extract.strip_types(source)
        assert stripped == expected, f"{source!r} stripped to {stripped!r}"
        assert lab_race_extract.strip_types(stripped) == stripped, "not idempotent"


def test_extraction_refuses_a_source_without_the_guard():
    """A future refactor that drops the counter must fail loudly, not silently."""
    unguarded = "async function openRun(runId: number) {\n  resetNotices()\n}\n"
    with pytest.raises(AssertionError):
        lab_race_extract._extract_counter(unguarded, "runRequestSeq")


def test_formalize_answering_after_a_switch_cannot_pull_the_page_back(seen):
    """Rewriting the draft, then switching: the late re-read is dropped."""
    state = seen["s12_after_formalize_answered_late"]
    assert state["runId"] == 2
    assert state["url"] == 2
    assert state["notice"] == "", "a stale formalize still spoke over the new record"


def test_the_422_branch_of_formalize_shares_the_same_ticket(seen):
    """The 422 re-read lives in the catch; it must obey the same ticket."""
    state = seen["s13_after_formalize422_answered_late"]
    assert state["runId"] == 2
    assert state["url"] == 2


def test_confirm_draft_answering_after_a_switch_is_dropped(seen):
    """A stale confirmation must not overwrite the newly opened research."""
    state = seen["s14_after_confirm_answered_late"]
    assert state["runId"] == 2
    assert state["url"] == 2


def test_a_chained_operation_is_not_cancelled_by_its_own_earlier_step(seen):
    """Confirm-then-re-read with no switch in between must still apply."""
    state = seen["s15_confirm_uninterrupted"]
    assert state["runId"] == 5
    assert state["url"] == 5
    assert "已确认" in state["notice"], "the chained confirmation lost its own result"


def test_a_compile_report_is_still_shown_even_when_a_ticket_exists(seen):
    """The 422 compile report is not the shown research: it must not be gated."""
    assert seen["s16_compile_report"] == {"result": "ERROR", "report": {"a": 1}}
    assert seen["s16_compile_report_draft"] == 77


def test_a_stale_compile_re_read_does_not_restore_the_old_run(seen):
    """handleCompileFailure's compensating read obeys the caller's ticket."""
    state = seen["s17_after_stale_compile_reread"]
    assert state["runId"] == 2
    assert state["url"] == 2


@needs_vue
def test_the_unguarded_shape_really_does_lose_the_race():
    """Show the defect is real, so the passing tests above mean something.

    This runs the pre-fix body (no sequence counter) against the very same
    transport and ordering as scenario 1: A asked first, B asked second, B
    answers first, A answers last. The page is expected to end up on A --
    the bug the guard removes.
    """
    unguarded = (
        "async function openRun(runId) {\n"
        "  resetNotices()\n"
        "  stopPolling()\n"
        "  loadingRun.value = true\n"
        "  try {\n"
        "    applyRun(await __tickApi.aiResearchRun(runId))\n"
        "  } catch (e) {\n"
        "    handleFailure(e)\n"
        "  } finally {\n"
        "    loadingRun.value = false\n"
        "  }\n"
        "}\n"
    )
    observed = _run_variant(unguarded)
    assert observed["runId"] == 1, "the pre-fix shape no longer reproduces the defect"
    assert observed["url"] == 1, "the pre-fix shape no longer rewinds the address bar"
