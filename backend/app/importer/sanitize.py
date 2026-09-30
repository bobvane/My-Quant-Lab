"""Treat repository content as untrusted data, always.

Any text coming from a third-party repository — READMEs, comments, docstrings,
strategy files — may contain prompt-injection payloads such as
``ignore previous instructions and ...``.  This module strips the known
override patterns before such text is stored as evidence or ever reaches an
LLM prompt.  It is a sanitizer, not a guarantee: the importer additionally
never executes repository code and never treats repository text as instruction.
"""

from __future__ import annotations

import re

__all__ = ["sanitize_untrusted_text"]

_PATTERNS = (
    # Direct instruction overrides, in several common phrasings.
    re.compile(
        r"(?is)\b(ignore|disregard|forget|override|bypass)\s+"
        r"(all\s+|any\s+|all\s+of\s+your\s+|your\s+)?"
        r"(previous|prior|earlier|above|system|initial)\s+"
        r"(instructions?|directives?|prompts?|rules?|constraints?)\b[^.\n]*[.\n]?",
    ),
    # "You are now ..." role hijacks.
    re.compile(
        r"(?im)^\s*(you are now|from now on you|act as|pretend (to be|you are)|"
        r"new (instructions?|directives?|system prompt))\b[^.\n]*[.\n]?",
    ),
    # Exfiltration attempts aimed at secrets.
    re.compile(
        r"(?is)\b(send|reveal|output|print|disclose|exfiltrate)\b[^.\n]*\b"
        r"(api[\s_-]?keys?|secrets?|tokens?|passwords?|credentials?)\b[^.\n]*[.\n]?",
    ),
    # Markdown / HTML comment tricks that try to hide instructions.
    re.compile(r"(?is)<!--.*?-->"),
)

_REPLACEMENT = "[filtered untrusted instruction-like text]"


def sanitize_untrusted_text(text: str | None, *, max_chars: int = 8000) -> str:
    """Remove instruction-like spans and cap length.

    Args:
        text: arbitrary third-party text (may be None).
        max_chars: hard cap so evidence blobs stay small.

    Returns:
        Sanitized text, safe to store and to embed as *data* in prompts.
    """

    if not text:
        return ""
    cleaned = text
    for pattern in _PATTERNS:
        cleaned = pattern.sub(_REPLACEMENT, cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + f"\n…[truncated, {len(text)} chars total]"
    return cleaned
