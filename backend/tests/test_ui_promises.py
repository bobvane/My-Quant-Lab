"""The UI document must describe the UI that ships (ADR-107).

``docs/13_UI_UX.md`` is what a reader consults before believing a page exists.
It used to promise a navigation tree with nine entries (five of them routes
nobody had written), a nine-part strategy detail page, a seven-step import
wizard and metric tooltips — all written in the present tense, almost none of it
in the frontend. A document that describes another product is worse than no
document, because it is believed.

These guards bind the promises to the code:

* §1 is the navigation: every row must match a route in
  ``frontend/src/main.ts`` and a label in ``frontend/src/App.vue``, in the same
  order, and the count must not silently shrink.
* every section must carry exactly one ``状态：`` line, and a section that is
  not ``已实现`` must say what is missing.
* ``已实现（<path>）`` must name files that exist.
* §3's nine parts must live on one detail page, each section reading its own source,
  and a missing reading must be named rather than left blank.
* §4's assumptions block must exist on the backtest page.
* §5's equity curve and latest-signal list must exist on the paper page.
* §6's four owed segments must exist on the signal page, and a "current" reading
  must name the moment it belongs to.
* §7's four questions must be answered for every metric label, from one source.
* §8's seven import steps must be declared in order and gated by evidence, and a
  passing validation must die with the text it was about.
* §9's phone layout must actually be a breakpoint in the stylesheet.
* the outstanding list in the last section must name exactly the sections that
  are not implemented.

Text-level on purpose: the frontend has no test runner in this suite, and the
defect was one document drifting away from code. The behaviour itself is checked
by eye and, for the assumptions block, by the module's own heading tests.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "docs" / "13_UI_UX.md"
MAIN = REPO_ROOT / "frontend" / "src" / "main.ts"
APP = REPO_ROOT / "frontend" / "src" / "App.vue"
BACKTEST = REPO_ROOT / "frontend" / "src" / "views" / "BacktestView.vue"
PAPER = REPO_ROOT / "frontend" / "src" / "views" / "PaperView.vue"
SIGNALS = REPO_ROOT / "frontend" / "src" / "views" / "SignalsView.vue"
STRATEGIES = REPO_ROOT / "frontend" / "src" / "views" / "StrategiesView.vue"
DETAIL = REPO_ROOT / "frontend" / "src" / "views" / "StrategyDetailView.vue"
API = REPO_ROOT / "frontend" / "src" / "api.ts"
METRICS = REPO_ROOT / "frontend" / "src" / "metrics.ts"
METRIC_HINT = REPO_ROOT / "frontend" / "src" / "components" / "MetricHint.vue"
STAT_CARD = REPO_ROOT / "frontend" / "src" / "components" / "StatCard.vue"
STYLE = REPO_ROOT / "frontend" / "src" / "style.css"
VIEWS = REPO_ROOT / "frontend" / "src" / "views"

_NAMED_LABEL = re.compile(r'(?<!:)label="(?P<label>[^"]+)"')
_MEDIA = re.compile(r"@media\s*\(max-width:\s*(?P<width>\d+)px\)\s*\{")

_ROUTE = re.compile(
    r"\{\s*path:\s*'(?P<path>[^']*)'\s*,\s*name:\s*'(?P<name>[^']*)'\s*,"
    r"\s*component:\s*(?P<component>\w+)"
)
_NAV = re.compile(r'<RouterLink to="(?P<path>[^"]+)">(?P<label>[^<]+)</RouterLink>')
_SECTION = re.compile(r"^## (?P<number>\d+)\. (?P<title>.+)$", re.MULTILINE)
_STATUS = re.compile(
    r"^状态：(?P<kind>已实现|部分实现|尚未实现)(?P<detail>（[^）]*）)?\s*$", re.MULTILINE
)
_OUTSTANDING_REF = re.compile(r"（第 (?P<number>\d+) 节")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _sections() -> list[tuple[int, str, str]]:
    """`(number, title, body)` for every `## N. Title` section of the spec."""
    text = _text(SPEC)
    found = list(_SECTION.finditer(text))
    assert found, "docs/13_UI_UX.md has no numbered sections: the spec was rewritten"
    sections: list[tuple[int, str, str]] = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        sections.append((int(match.group("number")), match.group("title"), text[match.end() : end]))
    return sections


def _documented_navigation() -> list[tuple[str, str]]:
    """`(label, path)` rows from the first fenced block of §1."""
    body = next(body for number, _, body in _sections() if number == 1)
    block = body.split("```text", 1)[1].split("```", 1)[0]
    rows: list[tuple[str, str]] = []
    for line in block.splitlines():
        if not line.strip():
            continue
        label, _, path = line.rpartition(" ")
        rows.append((label.rstrip(), path.strip()))
    return rows


def test_the_navigation_tree_is_the_shipped_navigation() -> None:
    """The tree is a promise about routes; the routes are in main.ts (ADR-107)."""
    documented = _documented_navigation()
    routes = [(match.group("path"), match.group("name")) for match in _ROUTE.finditer(_text(MAIN))]
    labels = [(match.group("path"), match.group("label")) for match in _NAV.finditer(_text(APP))]

    assert len(routes) >= 7, f"only {len(routes)} routes found; the route parser broke"
    assert documented == [(label, path) for path, label in labels], (
        "the navigation tree in docs/13_UI_UX.md §1 is not the navigation that ships "
        f"(App.vue: {[(path, label) for path, label in labels]}, "
        f"documented: {documented}): a page nobody routed is a page that 404s (ADR-107)"
    )
    # A detail route has a parameter and no navigation entry of its own: it may not
    # inflate the seven-row tree, but it may not hide from the spec either (ADR-114).
    nav_routes = [route for route in routes if ":" not in route[0]]
    detail_routes = [route for route in routes if ":" in route[0]]
    assert [path for _, path in documented] == [path for path, _ in nav_routes], (
        "the documented order no longer matches the route table in frontend/src/main.ts"
    )
    spec = _text(SPEC)
    for path, name in detail_routes:
        assert path in spec, (
            f"frontend/src/main.ts routes {path} ({name}) and docs/13_UI_UX.md never names it: "
            "an undocumented detail page is the nine-entry tree again, one page at a time "
            "(ADR-114)"
        )


def test_every_section_says_whether_it_exists() -> None:
    """Present tense for everything is how the nine-entry tree survived (ADR-107)."""
    sections = _sections()
    last = max(number for number, _, _ in sections)
    missing: list[str] = []
    for number, title, body in sections:
        # §1 is the navigation (checked against the router above) and the last
        # section is the summary: neither is a promise about a page.
        if number in (1, last):
            continue
        statuses = _STATUS.findall(body)
        if len(statuses) != 1:
            missing.append(f"§{number} {title}: {len(statuses)} 状态 lines")
            continue
        kind, detail = statuses[0]
        if kind != "已实现" and not detail:
            missing.append(f"§{number} {title}: {kind} without saying what is missing")
    assert not missing, "each section must carry one honest 状态 line: " + "; ".join(missing)


def test_an_implemented_promise_names_a_file_that_exists() -> None:
    """`已实现` has to point at the code that delivers it (ADR-107)."""
    broken: list[str] = []
    for number, title, body in _sections():
        for kind, detail in _STATUS.findall(body):
            if kind != "已实现":
                continue
            assert detail, f"§{number} {title} claims 已实现 without naming the file"
            named = [item.strip() for item in detail.strip("（）").split(",")]
            if not named:
                broken.append(f"§{number} {title}: empty file list")
            for relative in named:
                if not (REPO_ROOT / relative).exists():
                    broken.append(f"§{number} {title}: {relative} does not exist")
    assert not broken, "the spec cites files that are not there: " + "; ".join(broken)


def test_the_backtest_page_shows_the_assumptions_it_promises() -> None:
    """§4 promises a 查看假设 area; the page is where that promise lives (ADR-107)."""
    section = next(body for number, _, body in _sections() if number == 4)
    assert "查看假设" in section, "the spec no longer promises the assumptions area"

    page = _text(BACKTEST)
    assert "<h3>查看假设</h3>" in page, (
        "docs/13 §4 promises a 查看假设 area that the backtest page does not render: "
        "the assumptions used to be one row inside 结果可复现性 (ADR-107)"
    )
    for field in ("成交模型", "订单类型", "手续费", "滑点"):
        assert field in page, f"the assumptions area does not list {field}"


def test_the_paper_page_shows_the_curve_and_the_signals_it_promises() -> None:
    """§5 was the one promise that had neither a chart nor a list behind it (ADR-108)."""
    section = next(body for number, _, body in _sections() if number == 5)
    for promised in ("权益曲线", "最新信号"):
        assert promised in section, f"docs/13 §5 no longer promises {promised}"

    page = _text(PAPER)
    for rendered in ("<EquityChart", "最新信号", "curve_note", "paperEquity"):
        assert rendered in page, (
            f"docs/13 §5 promises a {rendered} the paper page does not use: the page drew "
            "the equity as four numbers and had no signal list (ADR-108)"
        )


def test_the_signal_page_shows_the_segments_it_promises() -> None:
    """§6 owed four segments: status, strategy history, holdings, risk (ADR-112)."""
    section = next(body for number, _, body in _sections() if number == 6)
    for promised in ("当前状态", "策略历史统计", "真实持仓上下文", "风险"):
        assert promised in section, f"docs/13 §6 no longer promises {promised}"

    page = _text(SIGNALS)
    for segment in ("当前状态", "策略历史统计", "真实持仓上下文", "风险 / 失效条件"):
        assert segment in page, (
            f"docs/13 §6 promises the {segment} segment and the signal page does not render "
            "it: the page stopped at 结果追踪 / 证据 / AI 解释 (ADR-112)"
        )

    # A "current" reading has to say which moment it belongs to, and that it is a
    # closed bar rather than a live quote.
    assert "最新已收盘 K 线" in page, (
        "the current-status segment must name the newest *closed* bar: a delayed reading "
        "that only says 当前状态 is read as real time (ADR-112)"
    )
    for source in ("api.latestBars(", "api.strategyEvidence(", "portfolio_context"):
        assert source in page, f"the signal page reads {source} nowhere: §6 needs it"

    api_text = _text(API)
    assert "strategyEvidence:" in api_text and "/signals/evidence/" in api_text, (
        "frontend/src/api.ts has no strategyEvidence method: the strategy-history segment "
        "has no data source (ADR-112)"
    )

    # No data must never be rendered as zero.
    for honest in ("不是零，是还没有数据", "还没有做过 AI 解释"):
        assert honest in page, (
            f"the signal page does not say {honest!r} when there is nothing to show: an empty "
            "box reads as a zero (ADR-112)"
        )


def test_the_professional_metrics_explain_themselves_in_place() -> None:
    """§7 promises four questions, next to the metric itself (ADR-110)."""
    section = next(body for number, _, body in _sections() if number == 7)
    for question in ("是什么", "怎么算", "为什么看它", "注意什么"):
        assert question in section, f"docs/13 §7 no longer promises the {question} question"

    for path in (METRICS, METRIC_HINT, STAT_CARD):
        assert path.exists(), (
            f"{path.relative_to(REPO_ROOT)} does not exist: §7 promises that every metric "
            "explains itself where it is read (ADR-110)"
        )

    metrics = _text(METRICS)
    for field in ("what:", "how:", "why:", "watch:"):
        assert field in metrics, (
            f"frontend/src/metrics.ts has no {field} field: every metric note answers "
            "the same four questions (ADR-110)"
        )

    hint = _text(METRIC_HINT)
    for question in ("是什么", "怎么算", "为什么看它", "注意什么"):
        assert question in hint, f"MetricHint.vue never renders the {question} question"
    assert "metricNote(" in hint, "MetricHint.vue does not look its note up in metrics.ts"
    assert "title=" in hint, "MetricHint.vue carries no hover tooltip, only the expanded list"

    card = _text(STAT_CARD)
    assert "<MetricHint" in card, (
        "the metric cards do not carry the in-place explanation: §7 promises that a metric "
        "can explain itself where it is read (ADR-110)"
    )

    declaration = re.search(r"NOT_A_METRIC[^=]*=\s*\[(?P<body>[^\]]*)\]", metrics)
    assert declaration, "frontend/src/metrics.ts no longer declares NOT_A_METRIC"
    declared = set(re.findall(r"'([^']+)'", declaration.group("body")))
    unexplained = sorted(
        {
            match.group("label")
            for path in sorted(VIEWS.glob("*.vue"))
            for match in _NAMED_LABEL.finditer(_text(path))
            if match.group("label") not in metrics and match.group("label") not in declared
        }
    )
    assert not unexplained, (
        f"these metric labels have neither a note in frontend/src/metrics.ts nor a place in "
        f"NOT_A_METRIC: {unexplained} — a professional metric either answers the four "
        "questions or is declared not to be one (ADR-110)"
    )


def test_the_import_wizard_gates_its_seven_steps() -> None:
    """§8 promised seven named steps; the page showed one long form (ADR-113)."""
    section = next(body for number, _, body in _sections() if number == 8)
    steps = (
        "Repository",
        "Analysis",
        "Detected Strategies",
        "Warnings",
        "DSL Preview",
        "Validation",
        "Import",
    )
    for step in steps:
        assert step in section, f"docs/13 §8 no longer promises the {step} step"

    page = _text(STRATEGIES)
    declaration = re.search(r"const WIZARD_STEPS = \[(?P<body>.*?)\] as const", page, re.DOTALL)
    assert declaration, (
        "the import page declares no seven-step list: §8 promises a wizard, and a form that "
        "looks like one without a step model cannot gate anything (ADR-113)"
    )
    order = [declaration.group("body").index(step) for step in steps]
    assert order == sorted(order), (
        f"the wizard declares its steps out of the promised order: {order} — the Import step "
        "cannot come before Validation (ADR-113)"
    )
    assert page.count("wizardStep ===") >= 7, (
        "fewer than seven step branches are rendered: a step in the chip list that has no body "
        "is a navigation entry pretending to be a page (ADR-113)"
    )
    assert "WIZARD_STEPS[wizardStep]" in page and "gotoStep(" in page, (
        "the wizard cannot move between steps, or never names the step it would open (ADR-113)"
    )

    # Every step is opened by evidence the reader can see, not by a click.
    for gate in ("stepUnlocked", "validatedDraft", "draftIsJson", "unsafeReviewed"):
        assert gate in page, (
            f"nothing gates a wizard step on {gate}: an ungated step is the old one long form "
            "with numbers in front of it (ADR-113)"
        )
    assert "importing || namedVersionTaken || !validatedDraft" in page, (
        "the import button is not gated on the validation result: §8's last step could be "
        "taken with an unvalidated draft (ADR-113)"
    )

    # Validation is a statement about one exact document, so editing the text has
    # to withdraw it, and the check itself must be reachable from the wizard.
    assert page.count("api.validateDsl(") >= 2, (
        "the wizard never validates the draft: only the manual DSL card calls the validator "
        "(ADR-113)"
    )
    assert "watch(dslText" in page and "validation.value = null" in page, (
        "a passing validation survives a change to the DSL text: the page would keep showing a "
        "verdict about a document nobody validated any more (ADR-113)"
    )
    assert "available_columns" in page, (
        "the validation step does not show the columns the validator knows, so a rejected name "
        "has no explanation next to it (ADR-113)"
    )

    # "Detected Strategies" must not be dressed up as strategy discovery.
    assert "扁平发现" in page, (
        "the Detected Strategies step implies the importer recognised whole strategies: it "
        "reports flat findings, and claiming more is a promise about someone else's code "
        "(ADR-113)"
    )


def _media_body(css: str, opening: int) -> str:
    """Body of the block whose `{` sits at *opening*, found by counting braces."""
    depth = 0
    for index in range(opening, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[opening + 1 : index]
    raise AssertionError("frontend/src/style.css has an unclosed block")


def test_the_phone_layout_is_a_checked_promise() -> None:
    """§9 was a preference with no breakpoint behind it (ADR-111)."""
    section = next(body for number, _, body in _sections() if number == 9)
    assert "style.css" in section, "docs/13 §9 no longer names the stylesheet that does it"
    assert STYLE.exists(), f"{STYLE.relative_to(REPO_ROOT)} does not exist"

    css = _text(STYLE)
    blocks = [
        _media_body(css, match.end() - 1)
        for match in _MEDIA.finditer(css)
        if int(match.group("width")) >= 480  # any phone is narrower than this
    ]
    assert blocks, (
        "frontend/src/style.css has no @media (max-width: ...) that covers phone widths: "
        "the sidebar stayed a fixed 232px column on a phone (ADR-111)"
    )
    phone = "\n".join(blocks)
    for rule in (".app-shell", ".sidebar", ".nav", ".main", "table"):
        assert rule in phone, f"the phone layout does not touch {rule} at all"
    assert "flex-direction: column" in phone, "the shell still lays out sideways on a phone"
    assert re.search(r"\.sidebar\s*\{[^}]*width:\s*100%", phone), (
        "the sidebar keeps its fixed width on a phone: the page would still be pushed "
        "sideways instead of stacked (ADR-111)"
    )
    assert "overflow-x: auto" in phone, (
        "wide tables have nowhere to scroll on a phone, so they widen the whole page"
    )


def test_the_outstanding_list_covers_exactly_the_unfinished_sections() -> None:
    """Two lists, one fact: the summary must not outlive the sections (ADR-107)."""
    sections = _sections()
    unfinished = {
        number
        for number, _, body in sections
        if any(kind != "已实现" for kind, _ in _STATUS.findall(body)) and number != 1
    }
    summary = next(body for number, _, body in sections if number == max(n for n, _, _ in sections))
    listed = {int(match.group("number")) for match in _OUTSTANDING_REF.finditer(summary)}

    assert listed == unfinished, (
        "the outstanding list at the end of docs/13_UI_UX.md and the per-section 状态 "
        f"lines disagree: sections {sorted(unfinished - listed)} are unfinished but not "
        f"listed, sections {sorted(listed - unfinished)} are listed but marked done (ADR-107)"
    )


# The nine parts §3 promises on one page, in the order the plan listed them.
DETAIL_SECTIONS = (
    "概览（Overview）",
    "血统（Provenance）",
    "版本历史（Version History）",
    "规则（Rules）",
    "回测（Backtest）",
    "样本外与滚动验证（OOS / Walk-forward）",
    "模拟盘（Paper Trading）",
    "当前信号（Current Signals）",
    "AI 解释（AI Explanation）",
)


def test_the_strategy_detail_page_shows_its_nine_sections() -> None:
    """§3's nine parts lived on four other pages; one page had to own them (ADR-114)."""
    section = next(body for number, _, body in _sections() if number == 3)
    for name in DETAIL_SECTIONS:
        assert name in section, f"docs/13 §3 no longer promises the {name} part"

    assert DETAIL.exists(), (
        "docs/13 §3 promises an independent strategy detail page and "
        "frontend/src/views/StrategyDetailView.vue does not exist: the nine parts stayed "
        "spread over /market, /backtest, /paper and /signals (ADR-114)"
    )
    page = _text(DETAIL)
    for name in DETAIL_SECTIONS:
        assert f"<h3>{name}</h3>" in page, (
            f"the detail page renders no {name} section: a part that exists only in the spec "
            "is the nine-part page the spec already had (ADR-114)"
        )

    # Every section reads a source of its own: a paragraph about a strategy is not a reading.
    for call in (
        "api.strategy(",
        "api.strategyLineage(",
        "api.strategyVersions(",
        "api.verifyStrategyVersion(",
        "api.backtests(",
        "api.lifecycle(",
        "api.paperAccounts(",
        "api.paperTrades(",
        "api.signalPreview(",
        "api.strategyEvidence(",
        "api.aiStatus(",
    ):
        assert call in page, (
            f"the detail page never calls {call}: a section rendered without its own source is "
            "prose, and prose cannot be checked (ADR-114)"
        )
    assert "api.explainSignalPreview(" in page or "api.explainBacktest(" in page, (
        "the AI section is not a request: §3 promises an AI explanation, and a section that "
        "explains nothing is a label (ADR-114)"
    )

    # A reading that does not exist must be named, because a blank reads as zero.
    for honest in (
        "不是零，是还没有数据",
        "未记录",
        "不落库",
        "归因按",
        "还没有配置 AI",
        "只解释已有数字",
    ):
        assert honest in page, (
            f"the detail page never says {honest!r}: §3's parts have holes (no backtest, no "
            "commits, no paper account, no AI provider), and an unnamed hole reads as a "
            "measurement (ADR-114)"
        )

    assert "/strategy/" in _text(STRATEGIES), (
        "nothing in the strategy library links to the detail route: the page would exist and "
        "no reader could reach it (ADR-114)"
    )
