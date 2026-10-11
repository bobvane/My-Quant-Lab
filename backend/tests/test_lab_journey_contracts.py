"""Guards for the Lab journey's frontend wiring (v2.5.0).

v2.5.0 claims a normal user can walk Research -> Draft -> Human Confirmation -> Compile ->
StrategyVersion -> Activate -> Backtest -> Experiment entirely in the browser. The backend
half of that claim is proven by the API-level tests (``test_ai_closure_end_to_end.py``,
``test_draft_confirmation.py``, ``test_experiments.py``); the frontend half had no test at
all, and the defect this milestone exists to remove -- a page that described a journey it
could not finish -- is exactly the kind a passing backend suite cannot see.

Like ``test_frontend_contracts.py``, these guards read the sources as text: they pin the
wiring that makes the journey reachable (the calls exist, the status is polled, the next
step exists), not Vue's rendering. They deliberately do not assert on wording beyond the one
sentence that used to promise the dead end and the two sentences that have to stay honest
about what the page does (materials, and what deleting an experiment spares).
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "src"
API = SRC / "api.ts"
LAB = SRC / "views" / "LabView.vue"

API_TEXT = API.read_text(encoding="utf-8")
LAB_TEXT = LAB.read_text(encoding="utf-8")

#: The sentence the page used to end on. Its absence is the point of the milestone.
DEAD_END_CLAIM = "不会生成可执行的策略"


#: Where the single ``api`` object literal starts. Members have to be looked up below it:
#: ``experiments`` is also the name of a field on ``ExperimentListOut``.
API_OBJECT = API_TEXT.index("export const api = {")


def _api_member(name: str) -> str:
    """Return the body of one member of the ``api`` object literal.

    The object is one flat literal, so a member ends where the next ``name:`` line at the
    same indentation begins -- matching that is safer than matching the closing brace of a
    nested call.
    """

    start = API_TEXT.index(f"\n  {name}:", API_OBJECT)
    rest = API_TEXT[start + 1 :]
    following = re.search(r"\n  \w+:", rest)
    return rest[: following.start()] if following else rest


def test_the_client_can_compile_a_confirmed_draft() -> None:
    """Compile is the call the old page never made (the journey's dead end)."""

    body = _api_member("compileStrategyDraft")
    assert "`/ai/strategy/drafts/${draftId}/compile`" in body
    assert "method: 'POST'" in body
    assert "strategy_id" in body


def test_the_client_can_activate_the_compiled_version() -> None:
    body = _api_member("activateVersion")
    assert "`/strategy-versions/${versionId}/activate`" in body
    assert "method: 'PUT'" in body


def test_a_refused_compile_keeps_its_business_code() -> None:
    """The gate answers 409 with ``error.code``; the client has to keep it to explain itself."""

    assert "readonly code: string | null" in API_TEXT
    assert "readonly details: Record<string, any>" in API_TEXT
    # The 422 body is `{result, report}` and has no error envelope at all, so the raw body
    # has to survive too (otherwise the compiler's own verdict is lost).
    assert "readonly body: Record<string, any> | null" in API_TEXT


def test_the_page_explains_the_confirmation_gate_instead_of_failing_obscurely() -> None:
    assert "COMPILE_ERROR_REASONS" in LAB_TEXT
    gate = LAB_TEXT[LAB_TEXT.index("COMPILE_ERROR_REASONS") :]
    for code in ("draft_not_confirmed", "draft_already_compiled"):
        assert code in gate
    assert "draft_not_confirmed" in LAB_TEXT


def test_the_page_polls_a_live_run_and_stops_on_its_own() -> None:
    """A research run now returns 202; without polling the page would look frozen."""

    assert re.search(r"const POLL_MS = \d+", LAB_TEXT)
    assert re.search(r"const POLL_FAILURE_LIMIT = \d+", LAB_TEXT)
    assert "window.setInterval(" in LAB_TEXT
    assert "window.clearInterval(" in LAB_TEXT
    # Leaving the page must not leave a timer behind.
    assert "onUnmounted(stopPolling)" in LAB_TEXT
    # A run that has decided is not polled again.
    assert "isLiveStatus(" in LAB_TEXT
    # ... and a run that cannot be read does not spin forever.
    assert "刷新状态" in LAB_TEXT


def test_the_page_offers_the_next_step_of_every_stage() -> None:
    """Compile -> Activate -> Backtest, each reachable from what the page just produced.

    v2.7.0 (ADR-191): the last leg carries the instrument too. The lab asks the user
    to pick one series once (``#lab-backtest-target``) and hands it over with ``run=1``,
    so the reader does not choose the same thing twice or press "run" again.
    """

    assert "api.compileStrategyDraft(" in LAB_TEXT
    assert "api.activateVersion(" in LAB_TEXT
    assert "compiled_strategy_version_id" in LAB_TEXT
    assert "/backtest" in LAB_TEXT
    # The handoff itself: the version, the instrument, and "run it now".
    assert "strategy_version_id: String(version.id)" in LAB_TEXT
    assert "symbol: target.symbol" in LAB_TEXT
    assert "run: '1'" in LAB_TEXT
    # ... and the instrument comes from stored series, chosen by the user, not invented.
    assert 'id="lab-backtest-target"' in LAB_TEXT
    assert "chosenBacktestTarget" in LAB_TEXT
    assert "api.series()" in LAB_TEXT


def test_the_lab_can_take_a_url_as_material() -> None:
    """The API has ingested url/pdf/github_file since v2.1.0; v2.7.0 wires up ``url``.

    The page must call the ingest endpoint *before* it starts a run (the run then
    carries ``snapshot_id``), and it must translate the server's refusal codes instead
    of printing the raw English reason at the reader (ADR-191).
    """

    assert "api.aiSourceUrl(" in LAB_TEXT
    assert "snapshot_id" in LAB_TEXT
    assert 'id="lab-source-url"' in LAB_TEXT or "id='lab-source-url'" in LAB_TEXT
    # The refusal is a result, and its reason is the server's code, translated here.
    assert "SOURCE_REFUSAL_TEXT" in LAB_TEXT
    assert "host_not_allowed" in LAB_TEXT
    assert "body?.detail" in LAB_TEXT


def test_the_lab_can_take_a_pdf_as_material() -> None:
    """A PDF the platform cannot fetch is handed over inline, then read back by snapshot.

    The file lives on the operator's disk, so the page reads it as base64 and posts it to
    ``/ai/sources/pdf``; the run then names the stored snapshot, so the bytes travel once.
    A PDF with no text layer is *not* an error (200 + ``parse_status="unsupported"``), but
    it is also not material: the page has to refuse it in Chinese rather than start a run
    that would fail at ingest (ADR-210).
    """

    assert "api.aiSourcePdf(" in LAB_TEXT
    assert "content_base64" in LAB_TEXT
    assert "readAsDataURL(" in LAB_TEXT
    assert 'id="lab-source-pdf"' in LAB_TEXT or "id='lab-source-pdf'" in LAB_TEXT
    assert 'type="file"' in LAB_TEXT or "type='file'" in LAB_TEXT
    assert (
        'accept="application/pdf,.pdf"' in LAB_TEXT or "accept='application/pdf,.pdf'" in LAB_TEXT
    )
    # The server's own byte cap, named once and enforced before the upload.
    assert "MAX_PDF_BYTES" in LAB_TEXT
    assert "一份最多 2 MiB" in LAB_TEXT
    # "Read but empty" is a result the page acts on, in the reader's words.
    assert "pdfUnreadableReason(" in LAB_TEXT
    assert "parse_status === 'unsupported'" in LAB_TEXT
    assert "本版不做 OCR" in LAB_TEXT
    # And the run names the snapshot: the bytes are never uploaded a second time.
    assert "kind: 'pdf'" in LAB_TEXT


def test_the_page_no_longer_promises_a_dead_end() -> None:
    assert DEAD_END_CLAIM not in LAB_TEXT


# --------------------------------------------------------------------------- #
# The experiment panel: the last leg (Experiment -> result -> history -> compare)
# --------------------------------------------------------------------------- #

#: member name -> (path fragment, method) the client has to use.
EXPERIMENT_MEMBERS = {
    "createExperiment": ("'/experiments'", "POST"),
    "experiments": ("`/experiments?limit=${limit}", None),
    "experiment": ("`/experiments/${id}`", None),
    "compareExperiments": ("`/experiments/compare?ids=${ids.join(',')}`", None),
    "deleteExperiment": ("`/experiments/${id}`", "DELETE"),
}


def test_the_client_persists_and_reads_experiments_back() -> None:
    """A result that only exists until the response ends is not an experiment."""

    for name, (path, method) in EXPERIMENT_MEMBERS.items():
        body = _api_member(name)
        assert path in body, name
        if method is not None:
            assert f"method: '{method}'" in body, name


def test_the_page_creates_reads_lists_and_deletes_experiments() -> None:
    for call in (
        "api.createExperiment(",
        "api.experiments(",
        "api.experiment(",
        "api.compareExperiments(",
        "api.deleteExperiment(",
    ):
        assert call in LAB_TEXT, call


def test_deleting_an_experiment_says_the_backtest_run_survives() -> None:
    """The page may not imply that removing the record removes the engine's run."""

    assert "window.confirm(" in LAB_TEXT
    assert "背后那次回测运行不会被删除" in LAB_TEXT


def test_a_persisted_experiment_is_reachable_from_the_address_bar() -> None:
    """Leaving and coming back has to find the same experiment, so it lives in the URL."""

    assert "?experiment=<id>" in LAB_TEXT
    assert "queryWith('experiment'" in LAB_TEXT
    assert "router.replace({" in LAB_TEXT


def test_the_open_research_run_survives_a_reload() -> None:
    """§C: a refresh may not drop which run the operator was reading.

    The run itself is on the server and the list can reopen it, but the page has to come
    back to it on its own -- the same way it already does for an experiment.
    """

    assert "?run=<id>" in LAB_TEXT
    assert "queryWith('run'" in LAB_TEXT
    assert "rememberOpenRun(" in LAB_TEXT
    assert "if (requestedRunId !== null) void openRun(requestedRunId)" in LAB_TEXT


def test_the_page_marks_unmeasurable_sensitivity_points_instead_of_ranking_them() -> None:
    """ADR-055: a point inside indicator warmup has no score and may not win a ranking."""

    assert "预热不足" in LAB_TEXT
    assert "ADR-055" in LAB_TEXT
    assert "resultWarmupUnmet(" in LAB_TEXT


def test_the_engine_payload_stays_in_a_fold() -> None:
    """§C: internal JSON may be available, but not as the page's main UI."""

    assert "技术细节（原始 JSON" in LAB_TEXT
    assert "JSON.stringify(" in LAB_TEXT


def test_the_page_no_longer_claims_it_cannot_read_the_web_or_pdf() -> None:
    """The API has ingested url/pdf/github_file since v2.1.0; ``url`` landed in 2.7.0.

    ``pdf`` landed with this change (ADR-210). The old sentence said the backend could
    fetch material but this page was not wired up to it. That was true then and is false
    now, so the page states what it does read (a pasted paragraph, a public URL, or a PDF
    handed over from disk) and keeps the honest limit about the rest (``github_file`` still
    has no field on this page).
    """

    assert "不会去抓网页或解析 PDF" not in LAB_TEXT
    assert "还没有接到这个页面上" not in LAB_TEXT
    # What the page does now, in the reader's words...
    assert "给一个网址" in LAB_TEXT
    assert "也可以给一个公开网页的地址" in LAB_TEXT
    assert "传一份 PDF" in LAB_TEXT
    # ... and what it still does not take, said out loud instead of quietly omitted.
    assert "GitHub" in LAB_TEXT


# --------------------------------------------------------------------------- #
# G7 / ADR-211: the compiler's questions, answered by a human
#
# The compiler has always emitted machine-readable questions (``rejections[]`` carrying
# ``user_decidable``, plus the ``USER_REQUIRED`` slots); nothing accepted an answer, and
# the only free-text channel there was (``confirmation.note``) is never read by the
# architect prompt. ADR-211 adds that channel as *Option A*: answers are **not** compiler
# input, and the server writes a **new** draft instead of editing the answered one.
#
# These guards pin the frontend half of that contract: the calls exist and carry the
# draft hash they were filled on, the controls follow the server's question list rather
# than a second copy of the mapping, the promise that the original is preserved is made
# *before* the submit button, a stale compile report is not shown as the current one, and
# answering never compiles.
# --------------------------------------------------------------------------- #

#: member name -> (path fragment, method) the client has to use.
ANSWER_MEMBERS = {
    "draftQuestions": ("`/ai/strategy/drafts/${draftId}/questions`", None),
    "answerDraftQuestions": ("`/ai/strategy/drafts/${draftId}/answers`", "POST"),
}


def _lab_top_level(name: str) -> str:
    """Return one top-level declaration's body from ``LabView.vue``.

    The SFC is a flat ``<script setup>``, so a declaration ends where the next
    top-level one begins. Matching that beats counting braces: these bodies contain
    template literals, nested objects and arrow functions.
    """

    for pattern in (
        f"\nasync function {name}(",
        f"\nfunction {name}(",
        f"\nconst {name} = computed(",
    ):
        start = LAB_TEXT.find(pattern)
        if start != -1:
            rest = LAB_TEXT[start + 1 :]
            following = re.search(r"\n(?:async function |function |const |// ---)", rest)
            return rest[: following.start()] if following else rest
    raise AssertionError(f"LabView.vue has no top-level {name}")


def test_the_client_can_read_the_compilers_questions() -> None:
    """The question list comes from the server; the page never builds one itself."""

    for name, (path, method) in ANSWER_MEMBERS.items():
        body = _api_member(name)
        assert path in body, name
        if method is not None:
            assert f"method: '{method}'" in body, name
    # The answer call has to carry the hash of the draft the form was filled on: the
    # server re-checks it and refuses with 409 without writing anything when it moved.
    assert "answered_draft_hash" in _api_member("answerDraftQuestions")


def test_the_page_renders_the_servers_questions_instead_of_a_second_copy() -> None:
    assert "api.draftQuestions(" in LAB_TEXT
    assert "api.answerDraftQuestions(" in LAB_TEXT
    # The control follows the server's ``kind``; the page owns no mapping of its own.
    for kind in ("'number'", "'enum'", "'text'", "'timeframe'", "'condition'", "'block'"):
        assert f"item.kind === {kind}" in LAB_TEXT, kind
    # What is answerable, and why something is not, is the server's call -- and the
    # reason is printed rather than the question being quietly dropped.
    assert "item.answerable" in LAB_TEXT
    assert "item.reason" in LAB_TEXT


def test_the_page_says_a_new_draft_will_be_created_before_submitting() -> None:
    """ADR-211: the operator is told the original is preserved *before* they submit."""

    assert "生成新的一版草案" in LAB_TEXT
    assert "原样保留" in LAB_TEXT
    assert "旧草案的确认对新草案不算数" in LAB_TEXT


def test_answering_never_compiles_and_needs_a_fresh_confirmation() -> None:
    body = _lab_top_level("submitAnswers")
    assert "api.answerDraftQuestions(" in body
    # Answering must not compile: the new draft has no confirmation yet, so the compile
    # call would be refused -- and making it here would hide that the human gate exists.
    assert "compileStrategyDraft(" not in body
    assert "loadQuestions(" in body


def test_a_stale_compile_report_is_not_shown_as_the_current_drafts_report() -> None:
    """A refresh or deep link may not mistake the old draft's report for the new one's."""

    assert "compileOutcomeDraftId" in LAB_TEXT
    body = _lab_top_level("currentCompileOutcome")
    assert "compileOutcomeDraftId.value === currentDraftId.value" in body


def test_the_page_keeps_what_the_user_typed_when_the_server_refuses() -> None:
    """409/422 must explain themselves in the reader's words without losing the form."""

    body = _lab_top_level("handleAnswerFailure")
    assert "draft_hash_mismatch" in body
    # The mismatch case reloads the questions *without* resetting the inputs.
    assert "loadQuestions(false)" in body
    # A draft the run has moved past is refused too, and that branch has to open the
    # current draft before it reloads the questions.
    assert "draft_superseded" in body
    assert "answer_refused" in body
    assert "answer_failed_the_gates" in body
