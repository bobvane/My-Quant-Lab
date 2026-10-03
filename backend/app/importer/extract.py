"""Static extraction of strategy logic from Python source.

Only :func:`ast.parse` is ever used — repository code is *parsed*, never
imported, compiled to bytecode for execution, or run.  Anything the analyser
cannot confidently map to the Strategy DSL becomes an ``UnknownFinding`` with
file/line evidence instead of an invented rule.  A file that does not parse at
all is reported as unparsed rather than as parsed: read and understood are two
different claims (ADR-059).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from app.importer.github_client import FetchCoverage, RepoFile
from app.importer.sanitize import sanitize_untrusted_text

__all__ = [
    "ANALYSIS_VERSION",
    "AnalysisResult",
    "Evidence",
    "IndicatorFinding",
    "ParamFinding",
    "RuleFinding",
    "SkippedFile",
    "UnknownFinding",
    "UnsafeFlag",
    "analyze_python_source",
    "analyze_repository_files",
    "build_coverage",
    "coverage_warnings",
]

# Bumped when the analysis report changes shape: 1.1.0 separates parsed from
# inventoried files, keeps each skip reason, and adds the coverage block; 1.2.0
# teaches the coverage block to blame the budget instead of the cap; 1.3.0 stops
# counting a Python file that did not parse as parsed (ADR-059).
ANALYSIS_VERSION = "1.3.0"

MAX_SNIPPET_CHARS = 400

# func-name (lowercased, dots stripped) -> DSL indicator type
INDICATOR_ALIASES: dict[str, str] = {
    "ema": "EMA",
    "exponentialmovingaverage": "EMA",
    "sma": "SMA",
    "simplemovingaverage": "SMA",
    "wma": "SMA",
    "rsi": "RSI",
    "relativestrengthindex": "RSI",
    "macd": "MACD",
    "atr": "ATR",
    "averagetruerange": "ATR",
    "bollinger": "BBANDS",
    "bbands": "BBANDS",
    "bollingerbands": "BBANDS",
}

# bare name / attribute -> canonical OHLCV column
COLUMN_ALIASES: dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
}

UNSAFE_IMPORTS = frozenset(
    {"os", "sys", "subprocess", "socket", "shutil", "pty", "ctypes", "pickle", "marshal"}
)
UNSAFE_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__", "input", "exit"})
UNSAFE_ATTRS = frozenset({"system", "popen", "exec", "spawn", "get", "post", "request", "urlopen"})

RISK_KEYWORDS = (
    "stop",
    "stop_loss",
    "stoploss",
    "stop-loss",
    "take_profit",
    "takeprofit",
    "target",
)


@dataclass(frozen=True)
class Evidence:
    path: str
    start_line: int | None
    end_line: int | None
    snippet: str


@dataclass(frozen=True)
class IndicatorFinding:
    kind: str  # DSL indicator type: EMA/SMA/RSI/MACD/ATR/BBANDS
    period: int | None
    source_name: str  # variable or expression it was assigned to
    evidence: Evidence


@dataclass(frozen=True)
class RuleFinding:
    op: str  # DSL comparison op
    left: str  # column name, indicator id, or number-as-string
    right: str
    evidence: Evidence


@dataclass(frozen=True)
class ParamFinding:
    name: str
    value: float | int | str
    evidence: Evidence


@dataclass(frozen=True)
class UnknownFinding:
    category: str
    detail: str
    evidence: Evidence


@dataclass(frozen=True)
class UnsafeFlag:
    category: str
    detail: str
    evidence: Evidence


@dataclass(frozen=True)
class SkippedFile:
    """A candidate file the fetch could not read, and why."""

    path: str
    reason: str


@dataclass
class AnalysisResult:
    # Split on purpose: a ``.md`` next to the code is inventoried, never parsed,
    # and counting it as "scanned" is how a report overstates its own coverage.
    files_parsed: list[str] = field(default_factory=list)
    files_inventoried: list[str] = field(default_factory=list)
    files_skipped: list[SkippedFile] = field(default_factory=list)
    # A file that was downloaded but did not parse contributed **nothing**, so it
    # cannot be counted as parsed: "read" and "understood" are different claims,
    # and only the second one is what the report is about (ADR-059).
    files_unparsed: list[SkippedFile] = field(default_factory=list)
    # Set by :func:`analyze_python_source` for the file it just looked at; the
    # repository-wide pass moves it into ``files_unparsed``.
    parse_error: str | None = None
    indicators: list[IndicatorFinding] = field(default_factory=list)
    rules: list[RuleFinding] = field(default_factory=list)
    params: list[ParamFinding] = field(default_factory=list)
    unknowns: list[UnknownFinding] = field(default_factory=list)
    unsafe_flags: list[UnsafeFlag] = field(default_factory=list)
    lookbacks: list[int] = field(default_factory=list)


def _evidence(path: str, node: ast.AST | None, source: str) -> Evidence:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    snippet = ""
    if node is not None:
        try:
            snippet = ast.get_source_segment(source, node) or ""
        except Exception:
            snippet = ""
    if not snippet and start is not None:
        lines = source.splitlines()
        snippet = "\n".join(lines[max(0, start - 1) : (end or start)])
    return Evidence(
        path=path,
        start_line=start,
        end_line=end,
        snippet=sanitize_untrusted_text(snippet, max_chars=MAX_SNIPPET_CHARS),
    )


def _const_number(node: ast.AST, constants: dict[str, Any]) -> float | int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        value = constants.get(node.id)
        if isinstance(value, (int, float)):
            return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_number(node.operand, constants)
        return -inner if inner is not None else None
    return None


def _func_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""


def _column_ref(node: ast.AST) -> str | None:
    """Resolve an expression to a canonical OHLCV column, if obvious."""
    if isinstance(node, ast.Name):
        return COLUMN_ALIASES.get(node.id.lower())
    if isinstance(node, ast.Attribute):
        return COLUMN_ALIASES.get(node.attr.lower())
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return COLUMN_ALIASES.get(sl.value.lower())
    return None


class _StrategyVisitor(ast.NodeVisitor):
    """Single pass collecting indicators, rules, params and warnings."""

    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source
        self.constants: dict[str, Any] = {}
        self.names: dict[str, str] = {}  # variable -> indicator id or column
        self.result = AnalysisResult()

    # -- imports / dangerous calls --------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0].lower()
            if top in UNSAFE_IMPORTS:
                self.result.unsafe_flags.append(
                    UnsafeFlag(
                        category="unsafe_import",
                        detail=f"imports module '{alias.name}' (never executed by importer)",
                        evidence=_evidence(self.path, node, self.source),
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = (node.module or "").split(".")[0].lower()
        if module in UNSAFE_IMPORTS:
            self.result.unsafe_flags.append(
                UnsafeFlag(
                    category="unsafe_import",
                    detail=f"imports from module '{node.module}' (never executed by importer)",
                    evidence=_evidence(self.path, node, self.source),
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _func_name(node.func)
        base = name.split(".")[-1]
        if base in UNSAFE_CALLS:
            self.result.unsafe_flags.append(
                UnsafeFlag(
                    category="unsafe_call",
                    detail=f"calls '{base}(...)' (never executed by importer)",
                    evidence=_evidence(self.path, node, self.source),
                )
            )
        elif isinstance(node.func, ast.Attribute) and node.func.attr in UNSAFE_ATTRS:
            recv = _func_name(node.func.value)
            if recv.split(".")[0] in (
                "os",
                "subprocess",
                "sys",
                "requests",
                "urllib",
                "socket",
            ):
                self.result.unsafe_flags.append(
                    UnsafeFlag(
                        category="unsafe_call",
                        detail=f"calls '{recv}.{node.func.attr}(...)' (never executed)",
                        evidence=_evidence(self.path, node, self.source),
                    )
                )
        self._maybe_indicator_call(node)
        self._maybe_rolling(node)
        self.generic_visit(node)

    # -- assignments: constants, params, indicator bindings --------------

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = _const_number(node.value, self.constants)
            if value is not None:
                self.constants[name] = value
                lowered = name.lower()
                if (
                    any(key in lowered for key in RISK_KEYWORDS)
                    or "period" in lowered
                    or "length" in lowered
                    or "window" in lowered
                ):
                    self.result.params.append(
                        ParamFinding(
                            name=name, value=value, evidence=_evidence(self.path, node, self.source)
                        )
                    )
            elif isinstance(node.value, ast.Call):
                ident = self._indicator_id_for_call(node.value)
                if ident is not None:
                    self.names[name] = ident
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            value = _const_number(node.value, self.constants)
            if value is not None:
                self.constants[node.target.id] = value
        self.generic_visit(node)

    # -- comparisons: trading rules --------------------------------------

    def visit_Compare(self, node: ast.Compare) -> None:
        self._maybe_rule(node)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._maybe_crossover(node)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # Pandas-style elementwise combination: (a > b) & (a.shift(1) <= ...).
        # This is the form real strategy code uses; keyword `and` would raise
        # on Series, so BoolOp alone would miss every real crossover.
        self._maybe_crossover(node)
        self.generic_visit(node)

    # -- helpers ----------------------------------------------------------

    def _indicator_id_for_call(self, node: ast.Call) -> str | None:
        # No recording here: visit_Call records every indicator call exactly
        # once via generic_visit, so this helper only resolves the id.
        kind = INDICATOR_ALIASES.get(_func_name(node.func).split(".")[-1])
        if kind is None:
            return None
        period = self._call_period(node)
        suffix = str(period) if period else "x"
        return f"{kind.lower()}{suffix}"

    def _maybe_indicator_call(self, node: ast.Call) -> None:
        # Bare calls (not assigned) are still evidence of indicator usage.
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            kind = INDICATOR_ALIASES.get(_func_name(node.func).split(".")[-1])
            if kind is not None:
                period = self._call_period(node)
                self.result.indicators.append(
                    IndicatorFinding(
                        kind=kind,
                        period=period,
                        source_name="",
                        evidence=_evidence(self.path, node, self.source),
                    )
                )

    def _call_period(self, node: ast.Call) -> int | None:
        for keyword in ("period", "length", "window", "timeperiod", "n", "span"):
            for kw in node.keywords:
                if kw.arg == keyword:
                    value = _const_number(kw.value, self.constants)
                    if value is not None:
                        return int(value)
        for arg in node.args:
            value = _const_number(arg, self.constants)
            if value is not None:
                return int(value)
        return None

    def _maybe_rolling(self, node: ast.Call) -> None:
        # e.g. close.rolling(20).max() -> breakout lookback of 20
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr.lower() not in ("max", "min"):
            return
        inner = node.func.value
        if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)):
            return
        if inner.func.attr.lower() != "rolling":
            return
        if not inner.args:
            return
        lookback = _const_number(inner.args[0], self.constants)
        if lookback is not None and lookback > 0:
            self.result.lookbacks.append(int(lookback))

    def _operand(self, node: ast.AST) -> str | None:
        column = _column_ref(node)
        if column is not None:
            return column
        if isinstance(node, ast.Name) and node.id in self.names:
            return self.names[node.id]
        number = _const_number(node, self.constants)
        if number is not None:
            return str(number)
        return None

    def _maybe_rule(self, node: ast.Compare) -> None:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return
        op_map = {
            ast.Gt: "gt",
            ast.GtE: "gte",
            ast.Lt: "lt",
            ast.LtE: "lte",
            ast.Eq: "eq",
            ast.NotEq: "ne",
        }
        op = op_map.get(type(node.ops[0]))
        if op is None:
            return
        left = self._operand(node.left)
        right = self._operand(node.comparators[0])
        if left is None or right is None:
            self.result.unknowns.append(
                UnknownFinding(
                    category="unresolved_rule",
                    detail="comparison references unknown expression "
                    "(kept as evidence, not a rule)",
                    evidence=_evidence(self.path, node, self.source),
                )
            )
            return
        self.result.rules.append(
            RuleFinding(
                op=op, left=left, right=right, evidence=_evidence(self.path, node, self.source)
            )
        )

    def _maybe_crossover(self, node: ast.BoolOp | ast.BinOp) -> None:
        # (a > b) & (a.shift(1) <= b.shift(1))  ->  crosses_above
        if isinstance(node, ast.BoolOp):
            if not isinstance(node.op, ast.And) or len(node.values) != 2:
                return
            first, second = node.values
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, ast.BitAnd):
                return
            first, second = node.left, node.right
        else:
            return
        if not (isinstance(first, ast.Compare) and isinstance(second, ast.Compare)):
            return
        if not (isinstance(first.ops[0], (ast.Gt, ast.Lt)) and len(first.ops) == 1):
            return
        if len(second.ops) != 1 or not isinstance(
            second.ops[0], (ast.LtE, ast.GtE, ast.Lt, ast.Gt)
        ):
            return
        if not (
            _is_shifted(first.left, second.left)
            and _is_shifted(first.comparators[0], second.comparators[0])
        ):
            return
        left = self._operand(first.left)
        right = self._operand(first.comparators[0])
        if left is None or right is None:
            return
        op = "crosses_above" if isinstance(first.ops[0], ast.Gt) else "crosses_below"
        self.result.rules.append(
            RuleFinding(
                op=op, left=left, right=right, evidence=_evidence(self.path, node, self.source)
            )
        )

    def _unknown_functions(self, source: str) -> None:
        # Custom strategy functions we do not understand stay UNKNOWN.
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                self.result.unknowns.append(
                    UnknownFinding(
                        category="custom_function",
                        detail=f"function '{node.name}' not mapped to DSL (kept as evidence)",
                        evidence=_evidence(self.path, node, source),
                    )
                )


def _is_shifted(current: ast.AST, previous: ast.AST) -> bool:
    """Check ``previous`` is ``current.shift(1)`` (same base expression)."""
    if not (
        isinstance(previous, ast.Call)
        and isinstance(previous.func, ast.Attribute)
        and previous.func.attr == "shift"
        and previous.args
        and _const_number(previous.args[0], {}) == 1
    ):
        return False
    try:
        return ast.dump(current) == ast.dump(previous.func.value)
    except Exception:
        return False


def analyze_python_source(path: str, source: str) -> AnalysisResult:
    """Parse one Python file and extract strategy findings.

    A file that does not parse returns an empty result with ``parse_error`` set,
    and is deliberately *not* recorded as an ``UnknownFinding``: a file we could
    not parse is not a construct we could not map, and counting it as one made
    "N construct(s) could not be mapped" the only trace of a file that yielded
    nothing at all (ADR-059).
    """
    visitor = _StrategyVisitor(path, source)
    try:
        tree = ast.parse(source)
    except Exception as exc:  # noqa: BLE001 - repository code is untrusted input
        # Any parse-time failure means "we did not understand this file", which is
        # exactly what ``files_unparsed`` is for. One hostile file must never be
        # able to abort the whole report, so this catches more than SyntaxError.
        visitor.result.parse_error = sanitize_untrusted_text(
            f"file does not parse as Python ({type(exc).__name__}): {exc}"
        )
        return visitor.result
    visitor.visit(tree)
    visitor._unknown_functions(source)
    return visitor.result


def analyze_repository_files(files: list[RepoFile]) -> AnalysisResult:
    """Analyse fetched files; non-Python files are inventoried, not parsed."""
    merged = AnalysisResult()
    for repo_file in files:
        if repo_file.content is None:
            merged.files_skipped.append(
                SkippedFile(path=repo_file.path, reason=repo_file.skipped_reason or "not fetched")
            )
            continue
        if not repo_file.path.lower().endswith(".py"):
            merged.files_inventoried.append(repo_file.path)
            continue
        partial = analyze_python_source(repo_file.path, repo_file.content)
        if partial.parse_error:
            # Downloaded, but nothing was understood: not a parsed file.
            merged.files_unparsed.append(
                SkippedFile(path=repo_file.path, reason=partial.parse_error)
            )
            continue
        merged.files_parsed.append(repo_file.path)
        merged.indicators.extend(partial.indicators)
        merged.rules.extend(partial.rules)
        merged.params.extend(partial.params)
        merged.unknowns.extend(partial.unknowns)
        merged.unsafe_flags.extend(partial.unsafe_flags)
        merged.lookbacks.extend(partial.lookbacks)
    return merged


def build_coverage(fetch: FetchCoverage, findings: AnalysisResult) -> dict[str, Any]:
    """Describe what the analysis read, and what it did not.

    One implementation for both callers (the analyze endpoint and the GitHub
    watcher), because two arithmetics would eventually disagree about whether a
    repository was fully reviewed -- and this number decides whether a draft may
    be imported without a human.
    """
    return {
        "analysis_version": ANALYSIS_VERSION,
        "candidate_files": fetch.candidate_files,
        "candidate_python_files": fetch.candidate_python_files,
        "cap": fetch.cap,
        "attempted_files": fetch.attempted_files,
        "downloaded_files": fetch.downloaded_files,
        "parsed_files": len(findings.files_parsed),
        "inventoried_files": len(findings.files_inventoried),
        "skipped_files": len(findings.files_skipped),
        "unparsed_python_files": len(findings.files_unparsed),
        "not_attempted_files": fetch.not_attempted_files,
        "unread_python_files": fetch.unread_python_files,
        "complete": fetch.complete,
        "max_seconds": fetch.max_seconds,
        "budget_exhausted": fetch.budget_exhausted,
    }


def coverage_warnings(coverage: dict[str, Any]) -> list[str]:
    """Turn a coverage block into the sentences a reviewer has to read."""
    messages: list[str] = []
    not_attempted = int(coverage["not_attempted_files"])
    if not_attempted:
        if coverage.get("budget_exhausted"):
            budget = coverage.get("max_seconds")
            limit = f"{float(budget):.0f}s" if budget is not None else "its time budget"
            messages.append(
                f"the fetch stopped after {limit}: {not_attempted} candidate file(s) were "
                "left unread. Raise the time budget or lower max_files to cover more; this "
                "report covers only the files listed above."
            )
        else:
            messages.append(
                f"{not_attempted} candidate file(s) were never fetched: the cap is "
                f"{coverage['cap']} of {coverage['candidate_files']} candidate file(s). "
                "Raise max_files to read more; this report covers only the files listed above."
            )
    if int(coverage["unread_python_files"]):
        messages.append(
            f"{coverage['unread_python_files']} Python file(s) were not read, so rules that "
            "live in them are missing from these findings."
        )
    unparsed = int(coverage.get("unparsed_python_files") or 0)
    if unparsed:
        messages.append(
            f"{unparsed} Python file(s) were downloaded but did not parse, so nothing in them "
            "was understood and the rules they declare are missing (see files_unparsed)."
        )
    skipped = int(coverage["skipped_files"])
    if skipped:
        messages.append(
            f"{skipped} file(s) were fetched but could not be read (see files_skipped)."
        )
    return messages
