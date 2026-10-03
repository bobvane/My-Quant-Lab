"""Guards for the frontend's promises to the backend (ADR-087, ADR-088, ADR-089).

Three classes of defect lived in the Vue views and could not be caught by a backend
test, because the backend was right every time:

* the API returns fractions (``0.0512``), and the signals panel printed them with
  ``formatNumber(...) + '%'`` — a factor of 100 next to a win rate that was already
  multiplied by 100 (ADR-087);
* two pages put every request in one ``Promise.all`` without a ``.catch`` each, so a
  single 5xx blanked six or nine panels at once (ADR-088);
* controls that claimed an ability the request never carried: the scan button posted
  no ``persist``, the paper account had a documented reset endpoint and no button,
  and a backtest request could not name a date window even though the API accepted
  one (ADR-089).

These tests read the sources as text, exactly like the script guards do: they pin the
shape of the contract, not Vue's rendering. A rule keyed on metric *names* is also
deliberate — Ghostfolio and the resource page receive 0-100 percentages and are
correct to append ``%`` themselves, so a blanket ban on ``'%'`` would be wrong.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VIEWS = REPO_ROOT / "frontend" / "src" / "views"
API = REPO_ROOT / "frontend" / "src" / "api.ts"
FORMAT = REPO_ROOT / "frontend" / "src" / "format.ts"

SIGNALS = (VIEWS / "SignalsView.vue").read_text(encoding="utf-8")
BACKTEST = (VIEWS / "BacktestView.vue").read_text(encoding="utf-8")
DASHBOARD = (VIEWS / "DashboardView.vue").read_text(encoding="utf-8")
SETTINGS = (VIEWS / "SettingsView.vue").read_text(encoding="utf-8")
STRATEGIES = (VIEWS / "StrategiesView.vue").read_text(encoding="utf-8")
PAPER = (VIEWS / "PaperView.vue").read_text(encoding="utf-8")
API_TEXT = API.read_text(encoding="utf-8")
FORMAT_TEXT = FORMAT.read_text(encoding="utf-8")

SRC = REPO_ROOT / "frontend" / "src"
COMPONENTS = SRC / "components"
APP = (SRC / "App.vue").read_text(encoding="utf-8")
MODE = (SRC / "mode.ts").read_text(encoding="utf-8")
WORDING = (SRC / "wording.ts").read_text(encoding="utf-8")
METRICS = (SRC / "metrics.ts").read_text(encoding="utf-8")
STAT_CARD = (COMPONENTS / "StatCard.vue").read_text(encoding="utf-8")
METRIC_HINT = (COMPONENTS / "MetricHint.vue").read_text(encoding="utf-8")
UI_SPEC = (REPO_ROOT / "docs" / "13_UI_UX.md").read_text(encoding="utf-8")

# Fields the outcome API sends as fractions. `_pct` in the name does not mean the
# value was multiplied by 100 — that is the whole bug (ADR-087).
RATIO_FIELDS = ("pnl_pct", "mae_pct", "mfe_pct", "avg_pnl_pct", "total_pnl_pct")


def _function_body(text: str, name: str) -> str:
    """Return the body of ``async function <name>(`` up to the closing brace."""

    start = text.index(f"async function {name}(")
    end = text.index("\n}\n", start)
    return text[start:end]


def _exported_function_body(text: str, name: str) -> str:
    """Return the body of ``export function <name>(`` up to the closing brace."""

    start = text.index(f"export function {name}(")
    end = text.index("\n}\n", start)
    return text[start:end]


def _promise_all_block(text: str) -> str:
    """Return the whole argument list of the first ``await Promise.all([``.

    This used to stop at the first ``])`` it could find, which is often the end of a
    *nested* call — ``.catch(() => note('模拟账户') ?? [])`` ends with exactly those two
    characters — so a page with ten requests could report "every request answers for
    itself" after inspecting three of them. Bracket depth is what "the block" means
    (ADR-102).
    """

    start = text.index("await Promise.all([")
    index = start + len("await Promise.all(")
    depth = 0
    while index < len(text):
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
        index += 1
    raise AssertionError("unbalanced `await Promise.all([` in the source")


def _bare_requests(block: str) -> list[str]:
    """Request lines inside a Promise.all that would reject the whole array."""

    return [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("api.") and ".catch(" not in line
    ]


def test_outcome_ratios_are_rendered_as_percentages() -> None:
    """A ratio printed with a hand-written `%` is off by 100 (ADR-087)."""

    for field in RATIO_FIELDS:
        wrong = re.findall(rf"formatNumber\([^)]*{field}[^)]*\)\s*\+", SIGNALS)
        assert wrong == [], f"{field} is a fraction, so it needs formatPercent: {wrong}"
        assert f"{field}" in SIGNALS
    assert SIGNALS.count("formatPercent(") >= len(RATIO_FIELDS) + 1
    # The win rate beside them was already a percentage; the two must agree now.
    assert "formatPercent(outcomeSummary.groups.ALL.win_rate)" in SIGNALS


def test_the_backtest_page_keys_its_units_on_the_metric_name() -> None:
    """`total_return` and `sharpe` sit in one table and are not the same unit."""

    assert "const RATIO_METRICS = new Set([" in BACKTEST
    for key in ("'total_return'", "'cagr'", "'max_drawdown'", "'win_rate'", "'exposure'"):
        assert key in BACKTEST, key
    assert "? formatPercent(value, 2) : formatNumber(value, digits)" in BACKTEST
    # Sharpe, profit factor and trade counts keep their own unit.
    block = BACKTEST[
        BACKTEST.index("const RATIO_METRICS") : BACKTEST.index("function formatMetric")
    ]
    for key in ("sharpe", "profit_factor", "number_of_trades"):
        assert f"'{key}'" not in block, f"{key} is not a fraction: {block}"
    # Every mixed-unit table goes through the helper.
    assert "formatMetric(k, oosResult.in_sample[k])" in BACKTEST
    assert "formatMetric(k, oosResult.out_of_sample[k])" in BACKTEST
    assert "formatMetric(m, r[m])" in BACKTEST
    assert "formatMetric(m.key, m.value)" in BACKTEST
    assert "formatMetric(sensMetric, p.objective)" in BACKTEST
    # The two sites that used to print a fraction with four decimals are gone.
    assert "formatNumber(r[m], 4)" not in BACKTEST
    assert "formatNumber(m.value, 4)" not in BACKTEST
    assert "formatNumber(p.objective)" not in BACKTEST


def test_every_dashboard_request_answers_for_itself() -> None:
    """One 500 in the first two panels used to blank the whole first screen."""

    block = _promise_all_block(DASHBOARD)
    assert _bare_requests(block) == [], _bare_requests(block)
    assert "api.systemInfo().catch(" in block
    assert "api.paperAccounts().catch(" in block
    # A failed module is named in the banner instead of taking the page down.
    assert "note('系统信息')" in block
    assert "加载失败，页面其余内容仍然可用" in DASHBOARD


def test_every_settings_request_answers_for_itself() -> None:
    block = _promise_all_block(SETTINGS)
    assert _bare_requests(block) == [], _bare_requests(block)
    for call in ("api.audit()", "api.settings()", "api.aiProviders()", "api.notificationConfig()"):
        assert f"{call}.catch(" in block, call
    # The values a failed request leaves behind are read defensively.
    assert "settings?.environment ?? {}" in SETTINGS
    assert "settings?.settings ?? []" in SETTINGS
    assert "providers.value = ai?.providers ?? []" in SETTINGS
    assert "if (notification) applyNotification(notification)" in SETTINGS


def test_a_scan_the_ui_promises_can_actually_store_signals() -> None:
    """The button said "扫描完成" while the request was a dry run (ADR-089)."""

    assert "scanSignals: (persist = false) =>" in API_TEXT
    assert "`/signals/scan?persist=${persist}`" in API_TEXT
    assert "api.scanSignals(true)" in SIGNALS
    assert "result.created" in SIGNALS
    assert "result.evaluated" in SIGNALS
    # The dashboard renders the dry-run result, so it keeps the default.
    assert "api.scanSignals()" in DASHBOARD


def test_the_documented_paper_reset_has_a_control_and_a_method() -> None:
    assert "resetPaperAccount: (accountId: number, initialCash?: number) =>" in API_TEXT
    assert "`/paper/accounts/${accountId}/reset${" in API_TEXT
    assert "api.resetPaperAccount(account.id)" in PAPER
    assert '@click="resetAccount(a)"' in PAPER


def test_a_backtest_can_name_its_date_window() -> None:
    assert "start?: string" in API_TEXT and "end?: string" in API_TEXT
    assert "...(start ? { start } : {})" in API_TEXT
    assert "...(end ? { end } : {})" in API_TEXT
    assert "const startDate = ref('')" in BACKTEST
    assert "const endDate = ref('')" in BACKTEST
    assert 'v-model="startDate" type="date"' in BACKTEST
    assert 'v-model="endDate" type="date"' in BACKTEST
    assert "startDate.value ? dayStart(startDate.value) : undefined" in BACKTEST
    assert "endDate.value ? dayEnd(endDate.value) : undefined" in BACKTEST
    # The window is inclusive and read as UTC, like the bars.
    assert "T00:00:00Z" in BACKTEST and "T23:59:59Z" in BACKTEST
    assert "起始日期不能晚于结束日期" in BACKTEST


def test_every_destructive_button_asks_first() -> None:
    """Four one-click deletes, one irreversible reset, no confirmation (ADR-089)."""

    cases = (
        (STRATEGIES, "deleteStrategy"),
        (STRATEGIES, "deleteSeries"),
        (BACKTEST, "removeRun"),
        (SETTINGS, "remove"),
        (PAPER, "resetAccount"),
    )
    for text, name in cases:
        body = _function_body(text, name)
        assert "window.confirm(" in body, f"{name} deletes without asking"
        assert body.index("window.confirm(") < body.index("api."), (
            f"{name} calls the API before the user confirms"
        )
        assert "if (!ok) return" in body, f"{name} ignores the answer"


def test_the_views_do_not_keep_a_second_copy_of_the_rule() -> None:
    """`formatPercent` is the one place that multiplies a metric by 100.

    The drawdown *chart* in BacktestView scales its own series to percent for the
    axis, which is a chart unit rather than a metric rendering, so only the signals
    panel — the one that printed five API fields by hand — is pinned here.
    """

    assert "* 100" not in SIGNALS
    assert "formatPercent" in SIGNALS and "formatNumber" in SIGNALS


def test_paper_pnl_is_the_realized_result_not_the_spent_cash() -> None:
    """`cash - net_deposits` is the P&L only while the account holds nothing.

    A buy that spends the whole balance leaves cash at zero and a position on the books
    (ADR-124), so a fresh full-size buy was printed as -100% on the paper card, in the
    paper table and on the dashboard.
    """

    body = _exported_function_body(FORMAT_TEXT, "paperPnlPct")
    assert "realizedPnl / netDeposits" in body
    assert "cash" not in body
    assert "realized_pnl: number" in API_TEXT
    for text in (PAPER, DASHBOARD):
        assert "formatPaperPnlPct(a.net_deposits, a.realized_pnl)" in text
        assert "toneOf(a.realized_pnl)" in text
        assert "a.cash - a.net_deposits" not in text


def test_the_settings_page_can_open_a_temporary_tunnel() -> None:
    """The temporary tunnel is a card on the settings page, and nothing more.

    The page may ask the API to open and close a public door; it may never run the
    command itself, hold a Cloudflare credential, or decide on its own how long the
    door stays open (ADR-125).
    """

    for field in (
        "url: string | null",
        "started_at: string | null",
        "expires_at: string | null",
        "remaining_seconds: number | null",
        "target_url: string",
    ):
        assert field in API_TEXT, field
    for method in (
        "temporaryAccess: () => request<TemporaryAccessState>('/settings/temporary-access')",
        "request<TemporaryAccessState>('/settings/temporary-access/start', { method: 'POST' })",
        "request<TemporaryAccessState>('/settings/temporary-access/stop', { method: 'POST' })",
    ):
        assert method in API_TEXT, method

    assert "<h3>临时远程访问</h3>" in SETTINGS
    # Every state the API can report has something on screen.
    for state in ("未开启", "正在启动", "● 已开启", "启动失败"):
        assert state in SETTINGS, state
    assert "正在等待 Cloudflare Tunnel 地址……" in SETTINGS
    assert "{{ temporaryAccess.url }}" in SETTINGS
    assert "剩余时间：{{ tunnelClock }}" in SETTINGS
    # The address dies with the tunnel, and the page says so.
    assert "此地址将在关闭或自动过期后失效" in SETTINGS
    # The card goes through the API for everything, and fails quietly on its own.
    assert "api.temporaryAccess()" in SETTINGS
    assert "api.startTemporaryAccess()" in SETTINGS
    assert "api.stopTemporaryAccess()" in SETTINGS
    assert "api.temporaryAccess().catch(() => note('临时远程访问'))" in SETTINGS
    # Copying is the browser's clipboard, not a shell command.
    assert "await navigator.clipboard.writeText(url)" in SETTINGS
    for forbidden in ("child_process", "require(", "exec("):
        assert forbidden not in SETTINGS, forbidden


def test_the_interface_has_a_basic_mode_that_hides_engineering_readings() -> None:
    """The default screen is for a person; the engineering readings are one click away.

    The audit's finding was not "too professional" but "two products in one page": the
    engine version, the dataset hash and the ``BUY/SELL/WAIT`` vocabulary all sat on the
    first screen (ADR-126). Hiding them is a *display* decision, so it lives in one
    client-side module and a handful of ``v-if``s — never in the data layer.
    """

    for symbol in ("mql-mode", "'basic' | 'advanced'", "isAdvanced", "setMode", "initMode"):
        assert symbol in MODE, symbol
    # The switch changes what is drawn, not what is asked for or computed.
    assert "api." not in MODE
    assert "fetch(" not in MODE

    assert "○ 普通模式" in APP and "● 高级模式" in APP
    assert '<template v-if="isAdvanced">' in APP
    assert '<RouterLink to="/resources">' in APP

    # Dashboard: 系统状态 / 版本 / 系统构成 are advanced-mode readings now. Counting the
    # attribute would be satisfied by gating *anything* three times (and stayed green with
    # four gates while the 系统状态 card was plain), so this asks which readings disappear.
    def _gated_stat_cards(source: str) -> tuple[set[str], set[str]]:
        gated: set[str] = set()
        always: set[str] = set()
        for card in re.findall(r"<StatCard\b.*?/>", source, re.S):
            label = card.split('label="', 1)[1].split('"', 1)[0]
            (gated if 'v-if="isAdvanced"' in card else always).add(label)
        return gated, always

    gated, always = _gated_stat_cards(DASHBOARD)
    assert {"系统状态", "版本"} <= gated
    assert {"可执行信号", "观察中"} <= always
    assert '<div v-if="isAdvanced" class="card"' in DASHBOARD

    # Settings: 运行环境 and 审计日志 are whole groups that only advanced mode shows.
    assert SETTINGS.count('<template v-if="isAdvanced">') >= 2

    # Signals: the raw rule ids and the feature catalogue are advanced-mode material.
    assert SIGNALS.count('v-if="isAdvanced') >= 2

    assert "## 10. 界面模式" in UI_SPEC
    assert "frontend/src/mode.ts" in UI_SPEC


def test_the_home_page_answers_four_questions_in_plain_words() -> None:
    """`/` answers four questions and nothing else, in that order (ADR-127).

    What used to be first — system status, version, database, Redis, feature version,
    DSL schema — is a status board, not an answer to "what should I do now".
    """

    for question in ("① 我在研究什么", "② 最近一次研究结论", "③ 下一步", "④ 需要你注意的事情"):
        assert question in DASHBOARD, question
    assert 'class="card answer-conclusion"' in DASHBOARD
    # The conclusion is a sentence, and it may say it cannot give one yet.
    assert "conclusionMissing" in DASHBOARD
    assert "answer-main" in DASHBOARD

    # ③ reads the lifecycle's own verdict instead of inventing a next stage.
    assert "suggested_next" in DASHBOARD
    assert "blocked_reason" in DASHBOARD
    assert "stagePage(" in DASHBOARD

    # ④ only lists warnings it can actually compute, and says so when there are none.
    assert "暂时没有需要特别注意的事情" in DASHBOARD

    # The four answers may not depend on a tenth request succeeding.
    block = _promise_all_block(DASHBOARD)
    assert "api.strategies().catch(" in block
    assert "api.signals(undefined, 50).catch(" in DASHBOARD
    assert "api.lifecycle(" in DASHBOARD


def test_every_professional_word_carries_a_plain_translation() -> None:
    """A metric says what it is before it says what it is called (ADR-127).

    The audit kept every professional term and asked for one thing: the first time a
    reader meets it, tell them what it is *for*. So the plain sentence sits on the card
    (``metricPlain``) and the alias sits next to the name (``termAlias``); the four
    questions stay behind one button, which is now labelled 详细解释.
    """

    for key in ("total_return", "max_drawdown", "sharpe", "win_rate", "profit_factor", "exposure"):
        assert key in WORDING, key
    for name in ("夏普比率", "最大回撤", "盈亏效率", "持仓时间占比", "收益 / 波动效率"):
        assert name in WORDING, name
    assert "export function termAlias(" in WORDING
    assert "export function metricKeyLabel(" in WORDING
    assert "export function metricPlain(" in METRICS

    # The first sentence is on the card, not behind a click.
    assert "metricPlain(label)" in STAT_CARD
    assert "termAlias(label)" in STAT_CARD
    assert "详细解释" in METRIC_HINT
    for word in ("是什么", "怎么算", "为什么看它", "注意什么"):
        assert word in METRIC_HINT, word

    # Backtest tables print the reader's name, not the engine's.
    assert "metricKeyLabel(m.key)" in BACKTEST


def test_signals_say_what_they_are_not() -> None:
    """A research signal must not read like an order, or like a price forecast.

    `BUY`/`SELL`/`WAIT` are the API's vocabulary; the page translates them and keeps one
    disclaimer next to the state and one next to the reference price (ADR-127).
    """

    assert "看多信号" in WORDING and "看空 / 退出信号" in WORDING and "暂不确认" in WORDING
    assert "这是策略研究信号，不是自动交易指令。" in WORDING
    assert "这是策略模型计算出的参考值，不代表未来价格预测。" in WORDING

    assert "signalLabel(" in SIGNALS
    assert "groupLabel(" in SIGNALS
    assert "SIGNAL_DISCLAIMER" in SIGNALS
    assert "REFERENCE_PRICE_DISCLAIMER" in SIGNALS
    # The page may not send the reader to our spec documents to understand it. A code
    # comment may still cite them; what the reader sees may not.
    template = SIGNALS.split("</script>", 1)[1]
    assert "docs/" not in template
