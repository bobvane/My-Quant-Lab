"""Prove from a pytest log that the PostgreSQL test backend was really offered.

``conftest.pytest_report_header`` writes exactly one line at the top of every pytest
run::

    postgres tests: localhost:5433/quantlab_test
    postgres tests: not offered (TEST_POSTGRES_URL is unset)

The second form is the one this script exists to catch. Two of the four
``... on PostgreSQL`` CI steps run modules that are backend-agnostic *by design* --
``test_draft_answers.py`` and ``test_paper_idempotency.py``. With no
``TEST_POSTGRES_URL`` they execute on in-memory SQLite, pass every assertion, and
report ``skipped == 0`` with the expected test count. Nothing in the JUnit report can
tell that run apart from the PostgreSQL one; the header can, because it states which
database the session was actually offered.

The check therefore requires *positive* evidence and fails closed:

* a missing log, an unreadable log, or no header line at all -> failure. ``-q`` (and
  ``--no-header``) remove the line entirely, so silence must fail the step rather than
  quietly pass it.
* the ``not offered`` form -> failure.
* a host line and a ``not offered`` line together -> failure: a contradiction is not
  evidence.

Only the part after the last ``@`` is ever printed by ``conftest``, so the line never
carries the password out of ``TEST_POSTGRES_URL``.

The line is matched at column 0, which is where pytest's terminal writer puts it. An
indented mention (a test that prints the phrase, a captured traceback) is not the
header and must not be accepted as proof.

Usage:  python scripts/verify_pg_dialect.py <path-to-pytest-log>
Exit:   0 when PostgreSQL was offered; non-zero otherwise (CI fails).
"""

from __future__ import annotations

import argparse
import sys

PREFIX = "postgres tests: "
NOT_OFFERED = "not offered"


def _dialect_lines(text: str) -> list[str]:
    """Every report-header line in the log, with the prefix stripped.

    Column 0 only: the header is written there, and accepting an indented copy would
    let captured test output stand in for the header.
    """

    return [
        line.rstrip("\r")[len(PREFIX) :]
        for line in text.splitlines()
        if line.rstrip("\r").startswith(PREFIX)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove a pytest log shows PostgreSQL was offered to the session."
    )
    parser.add_argument("log", help="the pytest output captured by the CI step")
    args = parser.parse_args(argv)

    try:
        with open(args.log, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except FileNotFoundError:
        sys.stderr.write(f"verify_pg_dialect: no pytest log at {args.log}\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"verify_pg_dialect: could not read {args.log}: {exc}\n")
        return 1

    lines = _dialect_lines(text)
    if not lines:
        sys.stderr.write(
            f"verify_pg_dialect: {args.log} carries no '{PREFIX}' line, so this run "
            "never proved which database it used. A missing header is not evidence "
            "(pytest drops it under -q / --no-header); refusing to guess.\n"
        )
        return 1

    refused = [line for line in lines if line.startswith(NOT_OFFERED)]
    if refused:
        sys.stderr.write(
            f"verify_pg_dialect: the session was not offered PostgreSQL "
            f"({PREFIX}{refused[0]}) -- these tests ran on SQLite while the step is "
            "named 'on PostgreSQL'\n"
        )
        return 1

    print(f"{PREFIX}{lines[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
