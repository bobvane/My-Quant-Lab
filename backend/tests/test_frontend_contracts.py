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
STYLE = (SRC / "style.css").read_text(encoding="utf-8")
STRATEGY_DETAIL = (VIEWS / "StrategyDetailView.vue").read_text(encoding="utf-8")
VITE_CONFIG = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
ENV_DTS = (SRC / "env.d.ts").read_text(encoding="utf-8")

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
    """`total_return` and `sharpe` sit in one table and are not the same unit.

    The rule lives in one place (``frontend/src/format.ts``) because the backtest page and
    the Lab experiment tables render the same engine metric keys; the page itself must not
    grow a second copy of it (ADR-087).
    """

    assert "const RATIO_METRICS = new Set([" in FORMAT_TEXT
    for key in ("'total_return'", "'cagr'", "'max_drawdown'", "'win_rate'", "'exposure'"):
        assert key in FORMAT_TEXT, key
    assert "if (RATIO_METRICS.has(key)) return formatPercent(value, 2)" in FORMAT_TEXT
    # Sharpe, profit factor and trade counts keep their own unit (a count is not a ratio).
    ratio_set = re.search(r"const RATIO_METRICS = new Set\(\[(.*?)\]\)", FORMAT_TEXT, re.S)
    assert ratio_set, "the ratio metric list is gone"
    for key in ("sharpe", "profit_factor", "number_of_trades"):
        assert f"'{key}'" not in ratio_set.group(1), f"{key} is not a fraction"
    # The page imports the shared helper instead of defining its own.
    assert "import { formatDateTime, formatMetric" in BACKTEST
    assert "function formatMetric" not in BACKTEST, "the backtest page kept a second unit rule"
    assert "RATIO_METRICS" not in BACKTEST, "the backtest page kept a second ratio list"
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


def test_the_comparison_table_borrows_the_unit_rule_instead_of_inventing_one() -> None:
    """The strategy-vs-benchmark table mixes ratios, a multiple and an amount.

    A local `kind` per row printed Sharpe as ``-23.15%`` and the final equity as
    ``2313459.84%`` in a real browser (Phase C UAT). Units come from the one registry in
    ``frontend/src/format.ts``, so each row carries its metric key (ADR-087, ADR-188).
    """

    assert (
        "function comparisonText(key: string, value: number | null | undefined): string" in BACKTEST
    )
    assert "return formatMetric(key, value)" in BACKTEST
    # Both columns go through it; no hand-picked unit survives on this table.
    assert "comparisonText(row.key, row.strategy)" in BACKTEST
    assert "comparisonText(row.key, row.other)" in BACKTEST
    assert "riskText('ratio', row.strategy)" not in BACKTEST
    assert "riskText('ratio', row.other)" not in BACKTEST
    # Rows key themselves on the registry's names, not on prose.
    for key in (
        "'total_return'",
        "'cagr'",
        "'annualized_volatility'",
        "'sharpe'",
        "'max_drawdown'",
        "'final_equity'",
    ):
        assert re.search(rf"key: {key}", BACKTEST), key
    # A conclusion that ends twice reads like a stutter.
    assert "endsWith('。。')" in BACKTEST


def test_every_dashboard_request_answers_for_itself() -> None:
    """One 500 in the first two panels used to blank the whole first screen."""

    block = _promise_all_block(DASHBOARD)
    assert _bare_requests(block) == [], _bare_requests(block)
    assert "api.paperAccounts().catch(" in block
    assert "api.signals(undefined, 50).catch(" in block
    # A failed module is named in the banner instead of taking the page down.
    assert "note('策略列表')" in block
    assert "加载失败，页面其余内容仍然可用" in DASHBOARD
    # The engineering readings are not asked for on this page at all (ADR-134).
    assert "api.health(" not in DASHBOARD
    assert "api.systemInfo(" not in DASHBOARD


def test_the_engineering_readings_live_in_the_settings_page() -> None:
    """The first screen answers "what should I do", not "what version is this" (ADR-134).

    The audit (§6) put it plainly: 系统状态 / 版本 / 引擎 / 数据库 / Redis / 特征版本 /
    DSL Schema are software-engineering readings. They stay in the product, they just
    belong to 系统管理 → 系统信息 rather than the first thing a user opens.
    """

    assert "系统构成" not in DASHBOARD
    assert "health" not in DASHBOARD
    assert '<RouterLink to="/settings">系统管理 → 系统信息</RouterLink>' in DASHBOARD

    block = _promise_all_block(SETTINGS)
    assert "api.health().catch(" in block
    assert "api.systemInfo().catch(" in block
    assert "note('系统信息')" in block
    assert "health.value = systemHealth" in SETTINGS
    assert "if (!systemHealth) healthError.value = '健康检查没有响应'" in SETTINGS
    assert "serverInfo.value = systemInfo" in SETTINGS
    assert '<h2 class="group-head">系统信息</h2>' in SETTINGS


def test_every_settings_request_answers_for_itself() -> None:
    block = _promise_all_block(SETTINGS)
    assert _bare_requests(block) == [], _bare_requests(block)
    for call in (
        "api.audit()",
        "api.settings()",
        "api.aiProviders()",
        "api.notificationConfig()",
        "api.health()",
        "api.systemInfo()",
    ):
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


def test_the_withdrawn_public_tunnel_left_nothing_behind_in_the_ui() -> None:
    """ADR-125 was withdrawn in v2.6.0; a half-deleted card is worse than none.

    v2.6.0 reduced the deployment to three containers and dropped the on-demand
    Cloudflare Quick Tunnel with it: the API routes, the manager, the binary and the
    settings page card are gone. What this pins is the failure mode of deleting only
    one side — a page that keeps polling `/settings/temporary-access` after the API
    stopped serving it shows a permanent "启动失败" the operator cannot clear.
    """

    for text, name in ((API_TEXT, "frontend/src/api.ts"), (SETTINGS, "SettingsView.vue")):
        for gone in (
            "temporaryAccess",
            "temporary-access",
            "cloudflared",
            "trycloudflare",
            "临时远程访问",
        ):
            assert gone not in text, f"{name} still carries {gone!r}; ADR-125 was withdrawn"


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
    # The sidebar's engineering readings are the shell's remaining advanced-only block;
    # the other one was the navigation group that left with the /resources page.
    assert 'v-if="isAdvanced" class="sidebar-meta' in APP
    # v2.6.0 retired the container-resource page with the 7→3 container work; nothing
    # in the shell may point at it again (the route is checked below).
    assert "/resources" not in APP

    # Dashboard: the engineering readings are gone from the first screen entirely
    # (ADR-134), so the question is no longer "which ones are gated" but "which page
    # carries them". Counting `v-if="isAdvanced"` would still be satisfied by gating
    # *anything* three times, so this asks which readings appear where.
    def _stat_card_labels(source: str) -> set[str]:
        return {
            card.split('label="', 1)[1].split('"', 1)[0]
            for card in re.findall(r"<StatCard\b.*?/>", source, re.S)
        }

    assert {"可执行信号", "观察中"} <= _stat_card_labels(DASHBOARD)
    assert "系统状态" not in _stat_card_labels(DASHBOARD)
    assert "版本" not in _stat_card_labels(DASHBOARD)
    assert '<h2 class="group-head">系统信息</h2>' in SETTINGS
    assert {"系统状态", "版本"} <= _stat_card_labels(SETTINGS)

    # Settings: 系统信息, 运行环境 and 审计日志 are whole groups only advanced mode shows.
    assert SETTINGS.count('<template v-if="isAdvanced">') >= 3

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
        "实验",
        "模拟验证",
        "信号",
        "数据",
        "AI 研究实验室",
        "系统管理",
    ]
    assert [path for path, _ in nav] == [
        "/",
        "/research",
        "/strategies",
        "/backtest",
        "/experiments",
        "/paper",
        "/signals",
        "/data",
        "/lab",
        "/settings",
    ]

    # The 高级 group title left with the page it pointed at (v2.6.0): every remaining
    # entry is reachable in both modes, and the shell's engineering readings are still
    # advanced-only (ADR-133).
    assert '<div class="nav-group">高级</div>' not in APP
    assert '<RouterLink to="/resources">' not in APP
    assert "{ path: '/resources'" not in MAIN_TS
    assert 'v-if="isAdvanced" class="sidebar-meta' in APP

    # The old address keeps working without becoming a navigation row of its own.
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


def test_the_typography_separates_a_conclusion_from_its_controls() -> None:
    """The audit (§18) asked to keep the look and rebuild the rank order (ADR-135).

    Before this, a form and a one-sentence conclusion were the same white box at the same
    weight, so the eye had no way in. Two moves carry it, and neither hides anything: the
    result card gets an accent rule and a larger reading, and the cards that only exist to
    be filled in step back to a dashed, dimmed frame (``card-quiet``).
    """

    # The result card leads: accent rule, heading in body colour, bigger number.
    assert ".card.answer-card," in STYLE
    assert ".card.conclusion-card," in STYLE
    assert ".card.comparison-card {" in STYLE
    assert "border-left: 3px solid var(--accent)" in STYLE
    assert ".conclusion-card .stat," in STYLE
    assert "font-size: 26px" in STYLE
    # The plain-language lead paragraph does not run the width of a desktop monitor.
    assert ".page-sub {" in STYLE
    assert "max-width: 72ch" in STYLE

    # The input cards step back on every page that has one.
    quiet = {
        "BacktestView.vue": BACKTEST,
        "PaperView.vue": PAPER,
        "StrategiesView.vue": STRATEGIES,
        "DataView.vue": DATA,
        "ResearchView.vue": RESEARCH,
    }
    for name, source in quiet.items():
        assert ".card-quiet {" in STYLE, name
        assert 'class="card card-quiet"' in source, name
    assert "border-style: dashed" in STYLE

    # The result cards keep their solid frame, and ④ 开始研究 is an action, not a form.
    assert 'class="card comparison-card"' in PAPER
    assert 'class="card conclusion-card"' in BACKTEST
    assert "<h3>④ 开始研究</h3>" in RESEARCH
    assert 'class="card card-quiet" style="margin-top: 14px">\n      <h3>④' not in RESEARCH

    assert "## 13. 排版层级" in UI_SPEC
    assert "frontend/src/style.css" in UI_SPEC


def test_the_version_comes_from_the_build_not_from_an_async_call() -> None:
    """The product review's P0-1: one version, on every page, at every moment.

    The sidebar and the footer used to print ``health?.version ?? '—'`` and
    ``?? '0.0.1'``. Both read a value that only exists after ``GET /health`` answers, so
    the version flickered to a placeholder — and the fallback number was wrong on
    purpose. The number is now baked in at build time, and the settings page shows the
    same constant instead of the backend's self-report.
    """

    assert "__APP_VERSION__" in VITE_CONFIG
    assert "readFileSync(new URL('./package.json', import.meta.url)" in VITE_CONFIG
    assert "JSON.stringify(version)" in VITE_CONFIG
    assert "declare const __APP_VERSION__: string" in ENV_DTS

    assert "const APP_VERSION = __APP_VERSION__" in APP
    assert "v{{ APP_VERSION }}" in APP
    assert "My Quant Lab v{{ APP_VERSION }}" in APP
    # No placeholder path survives: neither the em dash fallback nor the wrong number.
    assert "health?.version" not in APP
    assert "0.0.1" not in APP

    # The settings page's own "版本" reading is the same constant.
    assert ':value="APP_VERSION"' in SETTINGS
    assert "const APP_VERSION = __APP_VERSION__" in SETTINGS


def test_plain_mode_never_prints_a_raw_enum() -> None:
    """The product review's P0-2: an enum is an engine word, not a report.

    ``valid``, ``active``, ``pending`` and ``profitable`` used to appear verbatim in the
    data, paper, signals and strategy pages. Each one now goes through a translation
    table in ``wording.ts``; the raw value is still reachable, but only behind
    ``isAdvanced``.
    """

    for helper in (
        "accountStatusLabel",
        "signalStatusLabel",
        "outcomeLabel",
        "validationLabel",
        "providerLabel",
        "defaultSymbolFor",
    ):
        assert f"export function {helper}(" in WORDING, helper
    for table in (
        "ACCOUNT_STATUS_LABELS",
        "SIGNAL_STATUS_LABELS",
        "OUTCOME_LABELS",
        "VALIDATION_LABELS",
    ):
        assert f"export const {table}" in WORDING, table

    assert "accountStatusLabel(a.status)" in PAPER
    assert "accountStatusLabel(a.status)" in DASHBOARD
    assert "accountStatusLabel(account.status)" in STRATEGY_DETAIL
    assert "signalStatusLabel(s.status)" in SIGNALS
    assert "signalStatusLabel(s.status)" in PAPER
    assert "outcomeLabel(o.outcome_state)" in SIGNALS
    assert "validationLabel(version.validation_status)" in STRATEGY_DETAIL
    assert "validationLabel(v.validation_status)" in RESEARCH
    assert "validationLabel(v.validation_status)" in BACKTEST

    # No bare enum survives in those bindings.
    for bare in (
        "{{ a.status }}",
        "{{ s.status }}",
        "{{ o.outcome_state }}",
        "{{ account.status }}",
        "{{ version.validation_status }}",
        "{{ v.validation_status }}",
    ):
        for name, source in (
            ("SignalsView.vue", SIGNALS),
            ("PaperView.vue", PAPER),
            ("DashboardView.vue", DASHBOARD),
            ("StrategyDetailView.vue", STRATEGY_DETAIL),
        ):
            assert bare not in source, (name, bare)

    # The one raw quality value left on the data page is gated behind advanced mode.
    for name, source in (("DataView.vue", DATA), ("DashboardView.vue", DASHBOARD)):
        for line in source.splitlines():
            if "{{ s.quality_status }}" in line:
                assert 'v-if="isAdvanced"' in line, (name, line)


def test_a_disabled_button_says_why() -> None:
    """The product review's P0-4 and P1-10: a dead button has to explain itself.

    Every control that starts disabled now prints the missing ingredient next to
    itself, and the paper page's hand-typed signal id moved behind ``isAdvanced``:
    the ordinary way in is the signal list, which needs no id at all.
    """

    assert "const runBlockedReason = computed(" in BACKTEST
    assert "还差一个策略版本" in BACKTEST
    assert "还差一个标的代码" in BACKTEST
    assert 'v-if="runBlockedReason"' in BACKTEST

    assert "const startBlockedReason = computed(" in RESEARCH
    assert 'v-if="!canStart"' in RESEARCH

    assert 'v-if="!analyzing && !repoUrl.trim()"' in STRATEGIES
    assert 'v-if="!signalId"' in PAPER
    # The hand-typed id is a fallback, and the header says which way is the normal one.
    assert "<td>{{ signalStatusLabel(s.status) }}</td>" in PAPER
    assert "不必手抄信号 ID" in PAPER
    paper_execute = PAPER.index("执行信号（虚拟成交）")
    assert '<template v-if="isAdvanced">' in PAPER[paper_execute:]


def test_the_home_page_does_not_call_a_two_trade_sample_worth_it() -> None:
    """The product review's P0-3: two trades are not a verdict.

    The home page used to answer "整体是赚钱的：累计收益 0.19%" for a run with two
    trades. Under the same threshold the backend uses to gate its own lifecycle
    evidence, the page now refuses to conclude anything.
    """

    assert "const MIN_TRADES_FOR_VERDICT = 10" in DASHBOARD
    assert "样本太少（只有 ${trades} 笔交易），暂时不能判断这套策略是否有效" in DASHBOARD
    assert "还说明不了问题" in DASHBOARD
    assert "min_backtest_trades" in DASHBOARD


def test_the_ai_unavailable_note_is_chinese() -> None:
    """The product review's P1-5: the provider's own English string is not the message.

    ``aiStatus.note`` is the backend's sentence to an operator. The page now says what
    is missing and where to fix it, and keeps the original wording for advanced mode —
    where the reader is the operator.
    """

    assert "const aiUnavailableText = computed(" in DASHBOARD
    assert "AI 还没有配置" in DASHBOARD
    assert "isAdvanced.value && note" in DASHBOARD
    assert "（后端原话：${note}）" in DASHBOARD
    assert "AI 未配置：解释按钮不可用（{{ aiStatus.note }}）" not in DASHBOARD


def test_the_signal_page_does_not_say_loading_and_empty_at_once() -> None:
    """The product review's P1-6: "加载中…" and "没有信号。" were both true at once."""

    assert 'v-if="!loading && !signals.length"' in SIGNALS
    assert "加载中…" in SIGNALS


def test_no_page_starts_on_a_symbol_the_provider_cannot_serve() -> None:
    """The product review's P1-7 and P2-15: ``DEMO-AAPL`` under a real provider is empty.

    The demo provider serves exactly two symbols, so a hard-coded default guarantees a
    zero-bar sync as soon as the provider is ``yahoo_finance``. Both pages now ask the
    backend which provider is configured, suggest a symbol that provider can serve, and
    stop touching the field once the reader types in it.
    """

    for name, source in (("DataView.vue", DATA), ("BacktestView.vue", BACKTEST)):
        assert "ref('DEMO-AAPL')" not in source, name
        assert "symbolTouched" in source, name
        assert '@input="symbolTouched = true"' in source, name
        assert "api.systemInfo()" in source, name
    assert "defaultSymbolFor(" in DATA
    assert "defaultSymbolFor(marketProvider.value)" in BACKTEST
    assert "providerLabel(marketProvider)" in BACKTEST
    assert "providerText" in DATA
    assert "当前行情源" in DATA and "当前行情源" in BACKTEST


def test_a_narrow_screen_gets_its_own_breakpoint() -> None:
    """The product review's P1-8: at 375px the page scrolled sideways.

    The 820px breakpoint made ``.main`` scroll horizontally and forced a 560px table,
    which is wider than the phone. A second breakpoint wraps the parts that can wrap.
    """

    narrow = STYLE.split("@media (max-width: 480px)", 1)
    assert len(narrow) == 2, "no 480px breakpoint"
    block = narrow[1]
    assert "overflow-x: visible" in block
    assert "flex-wrap: wrap" in block
    assert ".grid.cols-3," in block
    assert ".grid.cols-4 {" in block
    assert "repeat(2, minmax(0, 1fr))" in block
    assert "min-width: 460px" in block


def test_the_settings_page_is_split_into_tabs() -> None:
    """The product review's P1-9: one very long scroll became four short pages.

    The tabs hide nothing that used to be visible in advanced mode — they use
    ``v-show``, so switching a tab re-renders nothing and every existing ``isAdvanced``
    gate still decides what a reading shows.
    """

    assert "type SettingsTab = 'ai' | 'notify' | 'system' | 'runtime'" in SETTINGS
    assert "const SETTINGS_TABS" in SETTINGS
    assert "const activeTab = ref<SettingsTab>('ai')" in SETTINGS
    assert 'class="tabs" role="tablist"' in SETTINGS
    assert 'role="tab"' in SETTINGS
    for tab in ("'ai'", "'notify'", "'system'", "'runtime'"):
        assert f'v-show="activeTab === {tab}"' in SETTINGS, tab
    # v-show, not v-if: no group loses its own advanced gate.
    assert SETTINGS.count('<template v-if="isAdvanced">') >= 4
    assert '<h2 class="group-head">系统信息</h2>' in SETTINGS
    # The AI diagnostics are readings, not the settings a reader has to fill in.
    assert "普通模式下只要上面这一张卡填好就能用 AI 解释了" in SETTINGS


def test_the_first_visit_gets_a_way_in() -> None:
    """The product review's P2-12 and P2-13: a first-time reader needs a starting point.

    The home page shows a dismissible four-step lead-in while there is no strategy yet,
    and points at the data page when the stored span is too short to conclude from.
    Neither is a new page, and neither decides anything.
    """

    assert "const GUIDE_KEY = 'mql-guide-dismissed'" in DASHBOARD
    assert "guideDismissed" in DASHBOARD
    assert "const showGuide = computed(" in DASHBOARD
    assert "function dismissGuide()" in DASHBOARD
    assert 'class="card guide-card"' in DASHBOARD
    assert "第一次用？按这四步走" in DASHBOARD
    assert "知道了，不再显示" in DASHBOARD
    assert '<RouterLink to="/data">到「数据」同步更长的一段</RouterLink>' in DASHBOARD


def test_dangerous_actions_look_dangerous() -> None:
    """The product review's P2-14: closing or deleting is not a neutral grey button.

    There is one ``.danger`` treatment (the data page's series delete, the strategy
    delete, the backtest run delete, the paper account's reset and the new close action),
    and closing an account explains what survives it before asking. Real render: the two
    paper buttons compute to ``rgb(217, 83, 79)``.
    """

    assert "button.danger {" in STYLE
    assert "button.danger:hover {" in STYLE
    assert "color: var(--sell)" in STYLE
    assert "function closeAccount(" in PAPER
    assert 'class="ghost danger"' in PAPER
    assert 'class="danger"' in PAPER
    body = _function_body(PAPER, "closeAccount")
    assert "window.confirm(" in body
    assert "if (!ok) return" in body
    assert "await setStatus(account, 'close')" in body
    # Every control that destroys stored research results carries the same treatment.
    assert 'class="ghost danger"' in DATA
    assert 'class="ghost danger"' in STRATEGIES
    assert 'class="ghost danger"' in BACKTEST


def test_the_research_page_does_not_print_the_quality_enum() -> None:
    """Real render (375/768/1440, /research): "质量：valid" was on the screen.

    The label table already existed, but this call site handed the raw value to the
    template, so basic mode printed the engine's own word. The raw value stays readable
    in advanced mode, like every other reading.
    """

    assert "质量：{{ qualityLabel(chosenOption.quality) }}" in RESEARCH
    # The raw value is still readable, but only inside the advanced-mode span.
    assert RESEARCH.count("{{ chosenOption.quality }}") == 1
    assert '<span v-if="isAdvanced" class="muted">（{{ chosenOption.quality }}）</span>' in RESEARCH
    assert "qualityLabel" in RESEARCH.split("</script>", 1)[0]


def test_a_wide_table_scrolls_inside_its_card() -> None:
    """Real render (1440px, /signals): the card holding a table pushed the whole page.

    ``table { width: 100% }`` next to ``white-space: nowrap`` cells gives a table a
    min-content width and no cap — measured 1168px inside a 1440px viewport, with no
    scroll container anywhere between that table and the page.
    """

    assert ".card:has(table) {" in STYLE
    block = STYLE.split(".card:has(table) {", 1)[1].split("}", 1)[0]
    assert "overflow-x: auto" in block


def test_the_data_page_default_is_one_of_its_own_options() -> None:
    """Real render (375px, /data): the lookback select drew as an empty box.

    ``lookbackDays`` defaulted to 400 while the options were 90/180/365/730/1825/3650, so
    the browser had no option to select and showed nothing. The options are one list now
    and the default is one of them.
    """

    assert "const LOOKBACK_OPTIONS" in DATA
    assert "const lookbackDays = ref(365)" in DATA
    options = re.search(r"const LOOKBACK_OPTIONS[^=]*= \[(.*?)\n\]", DATA, re.S)
    assert options is not None, "LOOKBACK_OPTIONS is not a literal list"
    days = {int(value) for value in re.findall(r"days: (\d+)", options.group(1))}
    assert days, "no option days found"
    default = int(re.search(r"const lookbackDays = ref\((\d+)\)", DATA).group(1))
    assert default in days, "the default value is not selectable"
    assert 'v-for="opt in LOOKBACK_OPTIONS"' in DATA


def test_the_strategy_library_does_not_claim_it_is_empty() -> None:
    """Real render (375px, /strategies): "还没有策略。" sat under a populated table.

    That paragraph was the ``v-else`` of the expanded-versions card, so it showed up
    whenever no row had been expanded — even with strategies in the library. The header
    row was also a column short of the body. Each table now owns its own empty state.
    """

    assert STRATEGIES.count("还没有策略。") == 1
    assert 'v-else class="muted">策略库还是空的' in STRATEGIES
    assert "<th>操作</th>" in STRATEGIES
