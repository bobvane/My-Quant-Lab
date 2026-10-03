"""Build a reviewable Strategy DSL draft from static findings.

The draft is conservative by design:

* only high-confidence findings (known indicator + known column/number) are
  mapped to rules;
* anything unresolved stays out of the DSL and appears in ``warnings``;
* a missing exit block is reported, never invented — an exit-less draft is
  returned as-is and the validator will reject it until a human adds exits.
"""

from __future__ import annotations

import re
from typing import Any

from app.importer.extract import AnalysisResult
from app.importer.github_client import RepoMeta

__all__ = ["build_draft_dsl"]

_KNOWN_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
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


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:64] or "imported-strategy"


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def build_draft_dsl(meta: RepoMeta, findings: AnalysisResult) -> tuple[dict[str, Any], list[str]]:
    """Return ``(draft_dsl, warnings)`` for human review."""

    warnings: list[str] = []

    seen_indicators: dict[tuple[str, int | None], dict[str, Any]] = {}
    for finding in findings.indicators:
        key = (finding.kind, finding.period)
        if key not in seen_indicators:
            spec: dict[str, Any] = {"id": finding.kind.lower(), "type": finding.kind}
            if finding.period is not None:
                spec["period"] = finding.period
            seen_indicators[key] = spec
    indicators = list(seen_indicators.values())

    entry_conditions: list[dict[str, Any]] = []
    skipped_rules = 0
    for rule in findings.rules:
        if rule.left in _KNOWN_COLUMNS and (rule.right in _KNOWN_COLUMNS or _is_number(rule.right)):
            entry_conditions.append({"op": rule.op, "left": rule.left, "right": rule.right})
        else:
            skipped_rules += 1
    if skipped_rules:
        warnings.append(
            f"{skipped_rules} detected rule(s) reference unknown columns and were "
            "left out of the draft (see unknowns with evidence)"
        )

    stop_multiple: float | None = None
    target_multiple: float | None = None
    for param in findings.params:
        lowered = param.name.lower()
        if not isinstance(param.value, (int, float)):
            continue
        value = float(param.value)
        # Only map values that look like multiples (small positive numbers on
        # a name mentioning multiple/ATR). A `stop_price = 100` is evidence,
        # not a multiple — mapping it would invent a nonsensical stop.
        looks_like_multiple = (
            "mult" in lowered or "atr" in lowered or "multiple" in lowered
        ) and 0 < value <= 20
        if not looks_like_multiple:
            continue
        if "stop" in lowered and stop_multiple is None:
            stop_multiple = value
        elif target_multiple is None and (
            "target" in lowered or "take" in lowered or "profit" in lowered
        ):
            target_multiple = value
    risk: dict[str, Any] = {"max_position_pct": 0.10}
    if stop_multiple:
        risk["stop_loss"] = {"type": "atr_multiple", "multiple": stop_multiple}
    if target_multiple:
        risk["take_profit"] = {"type": "risk_multiple", "multiple": target_multiple}
    if len(risk) == 1:
        warnings.append(
            "no stop-loss / take-profit parameters detected; "
            "a risk block with only position sizing was generated"
        )

    dsl: dict[str, Any] = {
        "schema_version": "1.0",
        "strategy": {
            "id": _slug(meta.repo),
            "name": meta.repo,
            "version": "1.0.0",
            "description": f"Imported from {meta.html_url} @ {meta.ref} (draft, needs review)",
            "source": {
                "type": "github",
                "repository": f"{meta.owner}/{meta.repo}",
                # The draft carries the revision it was built from, and only the
                # commit can be re-read later; `ref` is the name that was asked
                # for (ADR-060, docs/05 §4.4).
                "ref": meta.ref,
                "commit": meta.commit,
            },
        },
        "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
        "indicators": indicators,
        "features": sorted(
            {name for name in ("body_ratio", "close_position") if name in _KNOWN_COLUMNS}
        ),
        "entry": {"long": {"all": entry_conditions}} if entry_conditions else {},
        "exit": {},
        "risk": risk,
        "execution": {
            "fill_model": "next_bar_open",
            "fee_bps": 10,
            "slippage_bps": 5,
            "allow_fractional": True,
        },
    }

    if not entry_conditions:
        warnings.append(
            "no confident entry rules detected; the draft has an empty entry block "
            "and will not validate until rules are added manually"
        )
    warnings.append(
        "no exit rules are auto-generated; add exit conditions manually before importing"
    )
    if findings.unknowns:
        warnings.append(
            f"{len(findings.unknowns)} construct(s) could not be mapped to the DSL "
            "and need human review"
        )
    if findings.unsafe_flags:
        warnings.append(
            f"{len(findings.unsafe_flags)} unsafe construct(s) detected in repository code "
            "(imports, file/network access, eval); the importer never executes code, "
            "but review them before trusting the logic"
        )
    if not meta.license or meta.license.upper() in {"NOASSERTION", "OTHER"}:
        warnings.append(
            "repository license is missing or unrecognised; importing for personal "
            "research only and keep the provenance record"
        )
    return dsl, warnings
