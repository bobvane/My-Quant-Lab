"""Static validation for the Strategy DSL.

Catches unknown columns, malformed expressions and lookahead-prone constructs
*before* a backtest or a live scan is allowed to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.strategies.dsl import Condition, ConditionGroup, StrategySpec

__all__ = [
    "RESERVED_COLUMNS",
    "SUPPORTED_INDICATORS",
    "ValidationIssue",
    "ValidationReport",
    "validate_strategy",
]

# Columns that are always available in the feature frame.
BASE_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "timestamp",
}

# Guard against a strategy referencing something it can never know.
RESERVED_COLUMNS = {"future_close", "next_close", "tomorrow", "label", "target"}

# Indicator types the feature engine can materialise.
SUPPORTED_INDICATORS = {"EMA", "SMA", "RSI", "ATR", "MACD", "BOLLINGER", "BB", "BOLLINGER_BANDS"}
_PERIODIC_INDICATORS = {"EMA", "SMA", "RSI", "ATR", "BOLLINGER", "BB", "BOLLINGER_BANDS"}

KNOWN_OPERATORS = {
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "ne",
    "crosses_above",
    "crosses_below",
}

# Every indicator / feature registered by the feature engine.
KNOWN_DERIVED = {
    "ema20",
    "ema50",
    "sma20",
    "atr",
    "atr14",
    "rsi",
    "rsi14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_middle",
    "bb_upper",
    "bb_lower",
    "body_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "close_position",
    "range_atr_ratio",
    "overlap",
    "inside_bar",
    "outside_bar",
    "inside_bar_sequence",
    "micro_double",
    "breakout",
    "breakout_down",
    "breakout_follow_through",
    "breakout_failure",
    "prior_high",
    "prior_low",
    "distance_to_ema",
    "ema_relation",
    "ema_slope",
}


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    available_columns: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "issues": [i.as_dict() for i in self.issues],
            "available_columns": self.available_columns,
        }


def _walk(group: ConditionGroup | Condition | None, path: str) -> list[tuple[str, str, str]]:
    """Flatten a condition tree into ``(left, right, op)`` tuples with paths."""

    out: list[tuple[str, str, str]] = []
    if group is None:
        return out
    if isinstance(group, Condition):
        out.append((group.left, group.right, group.op))
        return out

    children = group.all if group.all is not None else (group.any or [])
    join = "all" if group.all is not None else "any"
    for index, child in enumerate(children):
        child_path = f"{path}.{join}[{index}]"
        out.extend(_walk(child, child_path))
    return out


def validate_strategy(
    spec: StrategySpec, available_columns: set[str] | None = None
) -> ValidationReport:
    """Validate a parsed strategy against the known feature set."""

    report = ValidationReport(issues=[])
    known = set(BASE_COLUMNS) | KNOWN_DERIVED
    if available_columns:
        known |= set(available_columns)

    # Declared indicators are materialised by the feature engine; add their id
    # (and any derived columns) to the known set, and flag unusable ones.
    for indicator in spec.indicators:
        kind = str(indicator.type).upper()
        known.add(indicator.id)
        if kind == "MACD":
            known.update({f"{indicator.id}_signal", f"{indicator.id}_hist"})
        if kind in {"BOLLINGER", "BB", "BOLLINGER_BANDS"}:
            known.update({f"{indicator.id}_upper", f"{indicator.id}_lower"})
        if kind not in SUPPORTED_INDICATORS:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "unsupported_indicator",
                    f"indicator '{indicator.id}' has unsupported type '{indicator.type}'",
                    f"indicators.{indicator.id}",
                )
            )
        elif (
            kind in _PERIODIC_INDICATORS
            and indicator.period is None
            and not getattr(indicator, "period_ref", None)
        ):
            report.issues.append(
                ValidationIssue(
                    "error",
                    "indicator_needs_period",
                    f"indicator '{indicator.id}' needs a 'period' or 'period_ref'",
                    f"indicators.{indicator.id}",
                )
            )
        ref = getattr(indicator, "period_ref", None)
        if ref and ref not in spec.parameters:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "unknown_parameter",
                    f"indicator '{indicator.id}' references unknown parameter '{ref}'",
                    f"indicators.{indicator.id}",
                )
            )

    report.available_columns = sorted(known)

    groups = {
        "entry.long": spec.entry.long,
        "entry.short": spec.entry.short,
        "exit.long": spec.exit.long,
        "exit.short": spec.exit.short,
    }

    for group_name, group in groups.items():
        if group is None:
            continue
        for left, right, op in _walk(group, group_name):
            if op not in KNOWN_OPERATORS:
                report.issues.append(
                    ValidationIssue("error", "unknown_operator", f"unsupported operator '{op}'")
                )
            for operand, side in ((left, "left"), (right, "right")):
                if operand in RESERVED_COLUMNS:
                    report.issues.append(
                        ValidationIssue(
                            "error",
                            "lookahead_reference",
                            f"{side} operand '{operand}' references unavailable future data",
                            f"{group_name}.{side}",
                        )
                    )
                elif operand not in known and not _is_number(operand):
                    report.issues.append(
                        ValidationIssue(
                            "error",
                            "unknown_column",
                            f"{side} operand '{operand}' is not a known feature column",
                            f"{group_name}.{side}",
                        )
                    )
            if not _is_number(right) and op in {"gt", "gte", "lt", "lte"}:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "cross_column_comparison",
                        f"'{op}' compares two series ('{left}' vs '{right}')",
                        group_name,
                    )
                )

    # Risk / execution sanity
    if spec.risk is None:
        report.issues.append(
            ValidationIssue(
                "warning", "missing_risk", "no risk block defined; exits must be explicit"
            )
        )
    if spec.risk and spec.risk.max_position_pct and spec.risk.max_position_pct > 0.5:
        report.issues.append(
            ValidationIssue(
                "warning",
                "large_position",
                "max_position_pct above 50% is aggressive for a single asset",
                "risk.max_position_pct",
            )
        )
    if spec.execution.fee_bps == 0 and spec.execution.slippage_bps == 0:
        report.issues.append(
            ValidationIssue(
                "warning",
                "zero_costs",
                "fee and slippage are both zero; results will look unrealistically good",
                "execution",
            )
        )
    if spec.execution.entry_order_type != "market":
        offset = (
            spec.execution.limit_offset_atr
            if spec.execution.entry_order_type == "limit"
            else spec.execution.stop_offset_atr
        )
        if not offset or offset <= 0:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "order_needs_offset",
                    f"entry_order_type '{spec.execution.entry_order_type}' needs a "
                    "positive ATR offset",
                    "execution",
                )
            )
    if spec.schema_version != "1.0":
        report.issues.append(
            ValidationIssue(
                "error",
                "unsupported_schema_version",
                f"schema_version '{spec.schema_version}' is not supported",
                "schema_version",
            )
        )
    return report


def _is_number(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True
