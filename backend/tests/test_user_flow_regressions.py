"""Regression guards for the second-round core user-flow check (ADR-221).

This round walked four real journeys as an ordinary user would:

    A. 首页 → AI 研究实验室
    B. AI 研究 → 研究结果 → 回测
    C. 回测 → 历史结果 → 样本外验证（OOS）
    D. 跨页面导航和反馈

Three defects came out of it. Each one below is a case where the page said one
thing and then did another, so the reader is left acting on a false belief:

1. "/backtest?strategy_version_id=<deleted>" silently kept whatever version
   "load()" had already defaulted to, so a run was executed against a strategy
   the user never picked.
2. "/lab?run=<id>" and "?experiment=<id>" were read once in "onMounted", so a link
   opened while the lab was already showing another record never switched.
3. The home page filtered research runs on "failed" only, so a run the backend
   ended as "rejected" (a real terminal status with a stored reason) fell through
   every bucket and the page claimed there was nothing to act on.

Like "test_frontend_contracts.py", these read the sources as text: they pin the
wiring that makes the honest behaviour reachable, not Vue rendering.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "src"

DASHBOARD = (SRC / "views" / "DashboardView.vue").read_text(encoding="utf-8")
LAB = (SRC / "views" / "LabView.vue").read_text(encoding="utf-8")
BACKTEST = (SRC / "views" / "BacktestView.vue").read_text(encoding="utf-8")

#: Start of the deep-link version lookup in "applyResearchQuery". The newline and
#: indentation matter: the assertion is that this exact two-line shape resolves the
#: id, not some other use of the same call.
_VERSION_LOOKUP = (
    "const all = await api.allStrategyVersions()\n"
    "      const target = all.find((v) => v.id === requestedVersion)"
)

#: The branch under test ends where the next query parameter is read.
_VERSION_BRANCH_END = "if (requestedSymbol)"


def _between(text: str, start_marker: str, end_marker: str) -> str:
    """The slice between two markers, so an assertion cannot be satisfied by a
    same-named token elsewhere in the file (the version id appears in several
    unrelated branches on this page)."""

    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_a_deleted_strategy_version_never_falls_back_to_another_one() -> None:
    """Flow B: a deep link naming a version that no longer exists must not lie.

    "applyResearchQuery" looked the requested version up and, when it was absent,
    fell through leaving "versionId" at whatever "load()" had already chosen
    ("strategies[0]"'s current version). The user then read the selector, saw a
    plausible version, and ran a backtest against a strategy they never chose --
    the exact failure the deep link exists to prevent.
    """

    branch = _between(BACKTEST, _VERSION_LOOKUP, _VERSION_BRANCH_END)

    # It must clear the selection rather than leave the unrelated default in place.
    assert "versionId.value = null" in branch, (
        "an unknown strategy_version_id must clear the selection, not keep the "
        "default that load() picked"
    )
    # And it has to name the id that failed, so the reader can tell a dead link from
    # a deleted version.
    assert "requestedVersion" in branch
    assert "已经不存在了" in branch
    # Only a version that really exists may arm the hand-over that later strips the
    # parameters out of the address bar: a dead id must leave the URL alone so the
    # warning survives a refresh.
    assert branch.count("handedOver = true") == 1


def test_backtest_still_accepts_a_valid_version_without_being_blocked() -> None:
    """The fix above must not break the working deep link (no false positives).

    The happy path -- a version that does exist -- still assigns the strategy,
    loads its versions and marks the request as handed over. If this regressed, the
    home page to BacktestView and LabView to BacktestView hand-offs would stop
    selecting anything.
    """

    branch = _between(BACKTEST, _VERSION_LOOKUP, _VERSION_BRANCH_END)

    assert "strategyId.value = target.strategy_id" in branch
    assert "versionId.value = requestedVersion" in branch


def test_lab_deep_links_follow_the_address_bar_after_the_first_open() -> None:
    """Flow A/D: a ?run= link must work even when /lab is already mounted.

    "requestedRunId" / "requestedExperimentId" were IIFE constants evaluated once,
    and no view watched "route.query". Clicking a home-page link while the lab was
    already open therefore left the previous record on screen while the address bar
    named another one -- the two disagreed and the user could not tell which record
    they were reading.
    """

    # Both deep links are watched, and each only re-reads when the named id changes.
    assert "() => queryId('run')" in LAB
    assert "() => queryId('experiment')" in LAB
    assert "id !== run.value?.run_id" in LAB
    assert "id !== openExperimentId.value" in LAB
    # The parse helper is shared instead of being copied per key.
    assert "function queryId(key: string)" in LAB
    # The ids are no longer frozen at setup time.
    assert "const requestedRunId = queryId('run')" in LAB
    assert "const requestedExperimentId = queryId('experiment')" in LAB


def test_rejected_research_is_visible_on_the_home_page() -> None:
    """Flow A: a run the backend ended as "rejected" must not vanish.

    "rejected" is a real terminal status ("research.py" calls
    "_finish(db, run, 'rejected', ...)" and stores the reason), but the home page
    bucketed runs on "failed" / live / capability-only. A rejected run therefore
    matched nothing and the page told the user there was nothing to act on, while
    the record sat there with a stored explanation.
    """

    assert "const labRejected = computed" in DASHBOARD
    assert "r.status === 'rejected'" in DASHBOARD
    # It reaches both the warnings list and the clickable card.
    assert "const rejected = labRejected.value" in DASHBOARD
    assert "labRejected.length" in DASHBOARD
    assert "[...labAwaitingDecisions, ...labRejected, ...labFailed, ...labLive]" in DASHBOARD
    # The empty-state sentence must no longer be reachable while a rejected run exists.
    assert "!labRejected.length" in DASHBOARD


def test_a_rejected_row_explains_itself_the_same_way_a_failed_one_does() -> None:
    """The home-page line for a rejected run must carry the stored reason.

    "run_summary" returns "error_message" for every terminal status, including
    "rejected". Printing only the bare label leaves the reader guessing; the failed
    rows already render the reason, so both terminal states must agree.
    """

    block = _between(DASHBOARD, "function labRunLine", "function labRunLink")

    assert "run.status === 'failed' || run.status === 'rejected'" in block
    assert "labRunFailureText(run.error_message)" in block
