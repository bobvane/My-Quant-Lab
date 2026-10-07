"""Machine checks on an AI explanation of a Phase C analysis (docs/30 §8.3).

The explainer contract has no numeric field at all, so a model cannot *store* a
number of its own. It can still write one into a sentence, and a sentence is what
a reader believes. These two checks close that gap on the way out:

* every numeric token in the text has to be a number the analysis produced --
  the same value, that value rounded to the digits the model wrote, or its
  percent form (``0.182`` may be written ``18.2%``);
* predictive wording is refused (``预计``, ``forecast``, ...), because an
  explanation describes a stored result, not a future one.

Both are deliberately tolerant about *presentation* and strict about *existence*:
rounding a real figure is allowed, inventing a plausible one is not. A rejected
explanation is never shown; the numbers it would have described still are
(ADR-189).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "NUMBER_PATTERN",
    "PREDICTION_PATTERN",
    "check_explanation",
    "collect_numbers",
    "find_prediction_phrases",
    "find_unsupported_numbers",
]

#: A number as a reader sees it: ``18``, ``-0.5``, ``18.2%``, ``10,000``.
NUMBER_PATTERN = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")

#: Wording that promises a future. Kept narrow on purpose: "should watch" is a
#: recommendation about attention, "should rise" is a prediction.
PREDICTION_PATTERN = re.compile(
    r"预计|预期|将会|将要|必将|应该会|有望|必然会|肯定会|保证(?:能|会|赚)?|稳赚|稳赢|必涨|必跌"
    r"|\b(?:expect(?:s|ed|ing)?|forecast(?:s|ed|ing)?|predict(?:s|ed|ing)?|"
    r"project(?:s|ed|ing)?|guarantee(?:s|d)?)\b",
    re.IGNORECASE,
)


def _number_text(token: str) -> str:
    """The digits of a token, without its percent sign or thousands separators."""

    body = token[:-1] if token.endswith("%") else token
    return body.replace(",", "")


def collect_numbers(payload: Any) -> set[float]:
    """Every number reachable in ``payload``.

    Numbers inside strings count (a date carries ``2024``, a caveat carries
    ``252``), because a model may restate what a label already says.
    """

    found: set[float] = set()

    def visit(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            if math.isfinite(float(value)):
                found.add(float(value))
            return
        if isinstance(value, str):
            for token in NUMBER_PATTERN.findall(value):
                try:
                    found.add(float(_number_text(token)))
                except ValueError:  # pragma: no cover - the regex cannot produce this
                    continue
            return
        if isinstance(value, Mapping):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)

    visit(payload)
    return found


def _decimals(token: str) -> int:
    body = _number_text(token)
    _, _, fraction = body.partition(".")
    return len(fraction)


def _is_the_same_number(token: str, allowed: float) -> bool:
    try:
        value = float(_number_text(token))
    except ValueError:  # pragma: no cover - the regex cannot produce this
        return False
    digits = _decimals(token)
    # A ratio may be written as a percentage and vice versa; either direction is
    # the same fact, so both candidates are checked.
    for candidate in (allowed, allowed * 100.0, allowed / 100.0):
        if not math.isfinite(candidate):
            continue
        if math.isclose(round(candidate, digits), value, rel_tol=0.0, abs_tol=1e-9):
            return True
    return False


def find_unsupported_numbers(explanation: Any, facts: Any) -> list[str]:
    """Numeric tokens in ``explanation`` that no number in ``facts`` supports."""

    allowed = collect_numbers(facts)
    unsupported: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            for token in NUMBER_PATTERN.findall(value):
                if token in seen:
                    continue
                if any(_is_the_same_number(token, candidate) for candidate in allowed):
                    continue
                seen.add(token)
                unsupported.append(token)
            return
        if isinstance(value, Mapping):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)

    visit(explanation)
    return unsupported


def find_prediction_phrases(explanation: Any) -> list[str]:
    """Predictive wording in ``explanation`` (order of appearance, de-duplicated)."""

    found: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            for match in PREDICTION_PATTERN.finditer(value):
                phrase = match.group(0)
                key = phrase.lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(phrase)
            return
        if isinstance(value, Mapping):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)

    visit(explanation)
    return found


def check_explanation(explanation: Any, facts: Any) -> list[dict[str, str]]:
    """All violations of the two rules above, in a shape a caller can report."""

    violations: list[dict[str, str]] = [
        {"code": "unsupported_number", "detail": token}
        for token in find_unsupported_numbers(explanation, facts)
    ]
    violations.extend(
        {"code": "prediction_wording", "detail": phrase}
        for phrase in find_prediction_phrases(explanation)
    )
    return violations
