"""Verify a PostgreSQL JUnit report (ADR-222).

Reads the JUnit XML pytest produced with ``--junitxml`` and requires exactly
``tests == --expect``, ``failures == 0``, ``errors == 0``, ``skipped == 0``. It is
the single source of truth for the acceptance verdict in every
``... on PostgreSQL`` CI step, and is kept in its own file instead of a heredoc
inside ``ci.yml`` so the workflow's YAML block scalar stays valid and the verdict
logic stays reviewable and testable.

The ``--expect`` count is what makes a skip visible: a module marked ``postgres``
skips (and pytest exits 0) when ``TEST_POSTGRES_URL`` is unset, so a step that only
reads the exit code reports success while proving nothing about PostgreSQL. Requiring
the exact count and ``skipped == 0`` turns that silent pass into a failure.

Usage:  python scripts/verify_b1_junit.py [--expect N] <path-to-junit.xml>
        --expect defaults to 5 (the B1 delete-guard module) for backward compatibility.
Exit:   0 when the verdict is met; non-zero otherwise (CI fails).
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET

DEFAULT_EXPECTED_TESTS = 5


def _count(tag: str, elem: ET.Element, path: str) -> int:
    """One JUnit count attribute, or a hard failure when it is not an integer."""

    raw = elem.get(tag, "0")
    try:
        return int(raw)
    except (TypeError, ValueError):
        sys.stderr.write(f"verify_b1_junit: non-integer {tag}={raw!r} on <{elem.tag}> in {path}\n")
        raise SystemExit(1) from None


def _verdict(root: ET.Element, path: str) -> tuple[int, int, int, int]:
    """The four counts, summed over the leaf suites when the root is ``testsuites``.

    For a ``testsuites`` root the root's own aggregate attributes are deliberately
    ignored (some writers leave them unset, others disagree with their children):
    summing the child ``testsuite`` elements is what prevents double-counting.
    """

    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
        if not suites:
            sys.stderr.write(
                f"verify_b1_junit: testsuites root in {path} has no testsuite children\n"
            )
            raise SystemExit(1)
        return (
            sum(_count("tests", s, path) for s in suites),
            sum(_count("failures", s, path) for s in suites),
            sum(_count("errors", s, path) for s in suites),
            sum(_count("skipped", s, path) for s in suites),
        )
    if root.tag == "testsuite":
        return (
            _count("tests", root, path),
            _count("failures", root, path),
            _count("errors", root, path),
            _count("skipped", root, path),
        )
    sys.stderr.write(f"verify_b1_junit: unrecognized JUnit root <{root.tag}> in {path}\n")
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a PostgreSQL JUnit report.")
    parser.add_argument("path", help="path to the JUnit XML pytest wrote")
    parser.add_argument(
        "--expect",
        type=int,
        default=DEFAULT_EXPECTED_TESTS,
        help=f"required number of tests (default {DEFAULT_EXPECTED_TESTS})",
    )
    args = parser.parse_args(argv)

    if args.expect <= 0:
        sys.stderr.write(f"verify_b1_junit: --expect must be positive, got {args.expect}\n")
        return 1

    try:
        root = ET.parse(args.path).getroot()
    except FileNotFoundError:
        sys.stderr.write(f"verify_b1_junit: no JUnit XML at {args.path}\n")
        return 1
    except ET.ParseError as exc:
        sys.stderr.write(f"verify_b1_junit: malformed JUnit XML {args.path}: {exc}\n")
        return 1

    tests, failures, errors, skipped = _verdict(root, args.path)
    print(f"{tests} {failures} {errors} {skipped}")

    if tests == args.expect and failures == 0 and errors == 0 and skipped == 0:
        return 0
    sys.stderr.write(
        f"verify_b1_junit: verdict not met: expected tests=={args.expect}, failures==0, "
        f"errors==0, skipped==0; got tests={tests} failures={failures} errors={errors} "
        f"skipped={skipped}\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
