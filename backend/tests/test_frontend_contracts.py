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
    start = text.index("await Promise.all([")
    end = text.index("])", start)
    return text[start:end]


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
