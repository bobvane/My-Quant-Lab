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
MAIN_TS = (SRC / "main.ts").read_text(encoding="utf-8")
RESEARCH = (VIEWS / "ResearchView.vue").read_text(encoding="utf-8")
DATA = (VIEWS / "DataView.vue").read_text(encoding="utf-8")

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
        # The series delete moved to the data page with the button it belongs to, so the
        # guard moved with it — the behaviour is still one-click-plus-confirm (ADR-131).
        (DATA, "deleteSeries"),
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


def test_the_backtest_result_answers_before_it_lists() -> None:
    """The result screen leads with a sentence, and the tiers stay reachable (ADR-128).

    The audit's finding about the backtest lab was ordering, not content: four metric
    cards said "here are numbers" and nothing said "here is what happened, how bad it can
    get, how much of this has been checked, and what to do next". So the conclusion card
    comes *before* the research tools, the professional metrics move one click down, and
    the advanced analyses stay behind the mode switch instead of being deleted.
    """

    for text in (
        "历史回测结论",
        'class="card conclusion-card"',
        "conclusionHeadline",
        "verdict",
        'class="risk-line"',
        'class="next-line"',
        "可信程度怎么样？",
        "evidenceRows",
        "evidenceSentence",
    ):
        assert text in BACKTEST, text

    # The first screen may not be the metrics table again.
    assert "MIN_MEANINGFUL_TRADES" in BACKTEST
    assert "min_backtest_trades" in BACKTEST  # the comment saying where 10 comes from
    assert BACKTEST.index("conclusion-card") < BACKTEST.index("权益曲线")

    # Order: conclusion, then the advanced (tier-3) tools, then the rest.
    marker = BACKTEST.index('<template v-if="isAdvanced">')
    close = BACKTEST.index("\n    </template>", marker)
    assert BACKTEST.index("conclusion-card") < marker
    gated = BACKTEST[marker:close]
    for heading in (
        "样本外验证（OOS）",
        "滚动 Walk-Forward",
        "参数敏感性分析",
        "Monte Carlo 重采样",
        "策略集成（加权投票）",
    ):
        assert heading in gated, heading
    assert "这次回测的提醒" not in gated
    # Basic mode still says the capability exists and where to switch.
    assert "高级分析（普通模式下不占第一屏）" in BACKTEST
    assert "● 高级模式" in BACKTEST

    # Tier 1 readings stay on the first screen; tier 2 is one click away, not gone.
    for label in ("总收益率", "最大回撤", "胜率", "交易次数", "盈亏效率", "年化复合收益率"):
        assert f'label="{label}"' in BACKTEST, label
    assert "查看详细分析" in BACKTEST
    assert "showDetailAnalytics" in BACKTEST
    assert '<table v-if="showDetailAnalytics">' in BACKTEST

    # A new metric card must explain itself in place (the audit's section 11 rule).
    for name in ("交易次数", "盈亏效率", "年化复合收益率", "平均持仓（根）"):
        assert name in METRICS, name
    assert "'平均持仓（根）':" in METRICS

    assert "查看详细分析" in UI_SPEC


def test_the_ai_summary_answers_five_questions_and_owes_none_of_them() -> None:
    """AI's job is to summarise stored facts, not to produce them (audit section 13, ADR-129).

    The five sections are 结论/原因/风险/可信程度/下一步. Four of them come from the
    explanation the backend stores; 可信程度 is computed here from lifecycle evidence, so
    it keeps working with no AI configured at all.
    """

    assert "AI 汇总（只解释已有数字，不重新计算）" in BACKTEST
    for section in ("① 结论", "② 原因", "③ 风险", "④ 可信程度", "⑤ 下一步"):
        assert section in BACKTEST, section

    # ② and ③ are the stored explanation's own fields, with a fallback for older results.
    assert "key_drivers" in BACKTEST and "why" in BACKTEST
    assert "risk_notes" in BACKTEST
    assert "what_to_watch_next" in BACKTEST
    assert "what_could_invalidate" in BACKTEST

    # ④ is local: the same evidence rows the card above is built from.
    assert "evidenceSentence" in BACKTEST
    assert "evidenceRows.value" in BACKTEST
    # Missing AI must not read as a missing conclusion.
    assert "未配置 AI" in BACKTEST

    assert "AI 汇总" in UI_SPEC


def test_the_paper_page_compares_itself_with_a_stored_backtest() -> None:
    """The paper account is a verification tool, so it must show backtest vs paper (ADR-130).

    The comparison joins nothing new: the backtest column is a *stored* run of the
    account's bound strategy and the paper column is the performance endpoint. What the
    page adds is the sentence the audit asked for — the two spans are different lengths,
    so the two returns may not be compared directly. That sentence is computed from the
    two equity curves' timestamps and is not an AI statement, because it must be true even
    when no AI provider is configured.
    """

    assert "回测 vs 模拟" in PAPER
    assert "不是记账工具，而是策略的验证工具" in PAPER
    assert "comparison" in PAPER and "loadComparison" in PAPER

    for call in (
        "api.strategyVersions(",
        "api.backtests(",
        "api.backtest(",
        "api.paperPerformance(",
        "api.paperEquity(",
    ):
        assert call in PAPER, call

    # The spans are described, and the page says the two columns are not comparable yet.
    assert "spanLabel" in PAPER
    assert "不能直接比大小" in PAPER
    assert "暂时不能与多年历史回测直接比较" in PAPER

    # No new numbers are invented: the columns are stored readings, printed as they are.
    assert "run.total_return" in PAPER and "perf.metrics?.total_return" in PAPER
    assert "run.max_drawdown" in PAPER and "perf.metrics?.max_drawdown" in PAPER
    assert "run.number_of_trades" in PAPER and "perf.closed_trades" in PAPER

    assert "回测 vs 模拟" in UI_SPEC


def test_the_navigation_splits_research_from_the_strategy_library() -> None:
    """One page called「行情与策略」became three entries with one job each (ADR-131).

    The audit's section 7 finding was about *responsibility*, not about length: syncing
    data, creating a strategy, versioning it and importing a repository were all on the
    same screen. So the split is checked here as a move, not as an addition — the data
    controls must exist on the data page and must be *gone* from the strategy page, or the
    job was merely duplicated.
    """

    nav = re.findall(r'<RouterLink to="([^"]+)">([^<]+)</RouterLink>', APP)
    assert [label for _, label in nav] == [
        "研究首页",
        "研究策略",
        "我的策略",
        "回测",
        "模拟验证",
        "信号",
        "数据",
        "系统资源",
        "系统管理",
    ]
    assert [path for path, _ in nav] == [
        "/",
        "/research",
        "/strategies",
        "/backtest",
        "/paper",
        "/signals",
        "/data",
        "/resources",
        "/settings",
    ]

    # The group heading and the engineering page are advanced-only; settings stays
    # reachable in both modes because the mode switch itself lives there (ADR-133).
    start = APP.index('<template v-if="isAdvanced">')
    gated = APP[start : APP.index("</template>", start)]
    assert '<div class="nav-group">高级</div>' in gated
    assert 'to="/resources"' in gated
    assert 'to="/settings"' not in gated

    # The old address keeps working without becoming a tenth navigation row.
    assert "{ path: '/market', redirect: '/strategies' }" in MAIN_TS
    assert "{ path: '/research'" in MAIN_TS
    assert "{ path: '/data'" in MAIN_TS
    assert "{ path: '/strategy/:strategyId'" in MAIN_TS

    # The move happened: data lives on the data page now.
    for text in (
        "同步行情",
        "api.syncMarketData(",
        "api.seriesDetail(",
        "api.deleteSeries(",
        "api.restoreSeries(",
        "QUALITY_MEANING",
        "quality_status",
        "bar_count",
        "显示已归档",
    ):
        assert text in DATA, text
    for status in ("valid", "partial", "invalid", "unknown"):
        assert f"status: '{status}'" in DATA, status
    # Engineering readings stay in advanced mode on the new page too.
    assert '<th v-if="isAdvanced">系列 ID</th>' in DATA
    assert "isAdvanced && rawSeries" in DATA

    for gone in ("syncData", "assetSymbol", "lookbackDays", "api.series("):
        assert gone not in STRATEGIES, gone
    # …and the strategy page still owns the library, its versions and the import wizard.
    assert "api.strategies(" in STRATEGIES
    assert "const WIZARD_STEPS = [" in STRATEGIES
    assert '"/strategy/"' in STRATEGIES or "/strategy/" in STRATEGIES

    assert "/research" in UI_SPEC and "/data" in UI_SPEC and "/market" in UI_SPEC


def test_the_research_page_walks_four_steps_and_hands_off_to_the_backtest() -> None:
    """「我想研究一个策略」is four questions, answered on one page (ADR-132).

    The handoff is a query string rather than a shared front-end store: the address is
    readable, it survives a refresh, and it lets the backtest page stay the only place
    that can start a run.
    """

    for step in ("① 选择标的", "② 选择策略", "③ 设置少量参数", "④ 开始研究"):
        assert f"<h3>{step}</h3>" in RESEARCH, step

    assert "router.push(" in RESEARCH
    for key in (
        "strategy_version_id",
        "symbol",
        "timeframe",
        "run: '1'",
        "start",
        "end",
        "size_mode",
        "size_fraction",
        "size_risk_pct",
    ):
        assert key in RESEARCH, key

    # Every value the handoff needs is already an existing endpoint.
    for call in (
        "api.assets(",
        "api.series(",
        "api.seriesDetail(",
        "api.strategies(",
        "api.strategyVersions(",
    ):
        assert call in RESEARCH, call

    # Data problems are stated before the run, not discovered after it.
    assert "quality_status" in RESEARCH
    assert "planSentence" in RESEARCH
    # Engineering readings (series id, source, hash) are advanced-only here as well.
    assert 'v-if="isAdvanced' in RESEARCH

    assert "研究策略" in UI_SPEC


def test_the_strategy_library_creates_from_plain_words() -> None:
    """A form writes the DSL, and the validator still decides (audit section 8, ADR-132).

    The JSON editor is not deleted, it moves behind the mode switch. The load-bearing
    detail is the order inside ``createFromForm``: the draft is regenerated from the form,
    then validated, and only a passing validation may create a version — a button may not
    skip the gate the import wizard has to respect.
    """

    for ref in (
        "formName",
        "formFast",
        "formSlow",
        "formTrendFilter",
        "formExitOnFastCross",
        "formStopAtr",
        "formTakeProfitR",
    ):
        assert f"const {ref} = ref(" in STRATEGIES, ref

    assert "function formDsl(" in STRATEGIES
    assert "formSentence" in STRATEGIES
    assert "formError" in STRATEGIES
    # The form owns the same draft the JSON editor edits, in that direction only.
    assert "watch(\n  [" in STRATEGIES
    assert "applyForm," in STRATEGIES
    assert "dslText.value =" in STRATEGIES

    body = _function_body(STRATEGIES, "createFromForm")
    assert body.index("await validate()") < body.index("await createStrategy()")
    assert "formError.value" in body

    # The form's own error check is about obvious mistakes only, never about rules.
    assert "策略名不能为空。" in STRATEGIES
    assert "快线周期需要小于慢线周期" in STRATEGIES
    assert "均线周期必须是正整数。" in STRATEGIES

    # The JSON editor survives, behind the advanced switch, and still validates.
    assert "<h3>策略 DSL（声明式，JSON 形式）</h3>" in STRATEGIES
    gated = STRATEGIES.index('<template v-if="isAdvanced">')
    assert gated < STRATEGIES.index("<h3>策略 DSL（声明式，JSON 形式）</h3>")
    assert "api.createStrategy(" in STRATEGIES
    assert STRATEGIES.count("api.validateDsl(") >= 2

    assert "人话" in UI_SPEC or "表单" in UI_SPEC


def test_the_backtest_page_accepts_a_handoff_without_trusting_it() -> None:
    """The backtest page may be told what to run, but the URL is user input (ADR-132).

    A query string arrives from a link, so every value is whitelisted before it reaches a
    form field, the query is cleared after it is applied (a refresh must not re-run a
    backtest), and the run itself goes through ``runNew`` — the same function the button
    calls, with the same validation.
    """

    assert "useRoute()" in BACKTEST and "useRouter()" in BACKTEST
    assert "applyResearchQuery" in BACKTEST
    assert "await applyResearchQuery()" in BACKTEST

    # Whitelists, not blind assignment.
    assert "RESEARCH_SIZE_MODES" in BACKTEST
    assert "DATE_ONLY" in BACKTEST
    assert "typeof raw === 'string'" in BACKTEST
    assert "api.allStrategyVersions(" in BACKTEST

    # Applied once, then removed from the address bar; the run is the page's own.
    assert "await router.replace({ path: '/backtest' })" in BACKTEST
    assert "if (shouldRun) await runNew()" in BACKTEST

    body = _function_body(BACKTEST, "applyResearchQuery")
    assert "router.replace" in body
    assert "runNew" in body
    for key in (
        "strategy_version_id",
        "symbol",
        "timeframe",
        "size_mode",
        "size_fraction",
        "size_risk_pct",
        "start",
        "end",
        "run",
    ):
        assert key in body, key
