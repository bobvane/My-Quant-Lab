"""The release version carries over at ten, and every file agrees with it.

Every component of a My Quant Lab version counts 0-9 and carries over at 10, so
the third field is always a single digit: v1.5.8 → v1.5.9 → v1.6.0. There is no
v1.5.10 -- and the release history up to v1.5.15 is exactly what happens when
the helper accepts one anyway (`set` only checked the shape of the number, while
`bump` already carried correctly). `set` now refuses an uncarried version, and
these guards pin the scheme down (ADR-078/ADR-079).

The second half matters for a different reason: `version.txt` is the single
source of truth, and the six files that mirror it can drift. The lock file once
sat at 0.9.8 while the application had already shipped 1.0.0, so the invariant is
asserted here rather than trusted.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "version.txt"
VERSION_SCRIPT = REPO_ROOT / "scripts" / "version.sh"
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PACKAGE_LOCK = REPO_ROOT / "frontend" / "package-lock.json"
INIT = REPO_ROOT / "backend" / "app" / "__init__.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
README = REPO_ROOT / "README.md"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required to exercise version.sh")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _released_version() -> str:
    return _text(VERSION_FILE).strip()


def _run_bash(
    snippet: str, *, cwd: pathlib.Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        [BASH, "-c", snippet],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_released_version_is_a_carried_version() -> None:
    """The third field is one digit: a version nobody can bump is not a version."""

    version = _released_version()
    assert re.fullmatch(r"v\d+\.\d+\.\d", version), (
        f"{version} breaks the carry rule (v1.5.9 → v1.6.0, never v1.5.10)"
    )
    minor = int(version.split(".")[1])
    assert minor <= 9, f"{version} cannot exist: minor versions count 0-9 (ADR-079)"


def test_the_documented_scheme_shows_the_carry() -> None:
    """The old header advertised v0.0.10, which is how the rule got broken."""

    header = _text(VERSION_SCRIPT).split("set -euo pipefail", 1)[0]
    assert "carries over at 10" in header
    assert "v1.5.9 → v1.6.0" in header
    assert "v0.0.10" not in header, "the documentation still shows an uncarried version"


def test_the_live_documents_do_not_teach_an_uncarried_version() -> None:
    """ADR-079 fixed the script and left the same wrong example in the README.

    The scheme is documented in places people actually read; the script is the
    only one that cannot lie, because it refuses. A live document that shows
    ``v0.0.10`` as the rule teaches the version the code rejects (ADR-085).
    History rows in ``docs/15`` may still quote it while describing the fix, so
    only the README -- what an operator reads before tagging -- is asserted.
    """

    readme = _text(README)
    assert "v0.0.10" not in readme, "the README still shows the uncarried example"
    assert "v1.6.9 → v1.7.0" in readme, "the README no longer shows the carry"


def test_the_readme_states_which_versions_get_deployed() -> None:
    """ADR-086: patches are pushed, only X.Y.0 reaches the NAS."""

    readme = _text(README)
    assert "ADR-086" in readme
    assert "X.Y.0" in readme


def test_every_version_reference_agrees_with_version_txt() -> None:
    """version.txt is the source of truth; the mirrors must not drift."""

    plain = _released_version().removeprefix("v")

    assert re.search(rf"^MQL_VERSION={re.escape(plain)}$", _text(ENV_EXAMPLE), re.MULTILINE)
    assert f'__version__ = "{plain}"' in _text(INIT)
    assert re.search(rf'^version = "{re.escape(plain)}"$', _text(PYPROJECT), re.MULTILINE)

    manifest = json.loads(_text(PACKAGE_JSON))
    assert manifest["version"] == plain
    lock = json.loads(_text(PACKAGE_LOCK))
    assert lock["version"] == plain, "the lock file top level drifted"
    assert lock["packages"][""]["version"] == plain, "the lock file root entry drifted"


@needs_bash
def test_an_uncarried_version_is_refused_and_changes_nothing() -> None:
    """`set v1.5.16` is the door the rule was broken through; it is now shut."""

    before = _text(VERSION_FILE)
    result = _run_bash("bash scripts/version.sh set v1.5.16", cwd=REPO_ROOT)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "carries over at 10" in result.stderr
    assert "one digit" in result.stderr
    assert _text(VERSION_FILE) == before, "a refused version still rewrote version.txt"


@needs_bash
def test_a_carried_version_is_accepted_and_synced_everywhere(tmp_path: pathlib.Path) -> None:
    """The six-place sync, exercised in a throwaway copy of the tree."""

    (tmp_path / "scripts").mkdir()
    shutil.copy(VERSION_SCRIPT, tmp_path / "scripts" / "version.sh")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "version.txt").write_text("v1.5.15\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("MQL_VERSION=1.5.15\n", encoding="utf-8")
    (tmp_path / "backend" / "app" / "__init__.py").write_text(
        '__version__ = "1.5.15"\n', encoding="utf-8"
    )
    (tmp_path / "backend" / "pyproject.toml").write_text(
        '[project]\nname = "my-quant-lab"\nversion = "1.5.15"\n', encoding="utf-8"
    )
    (tmp_path / "frontend" / "package.json").write_text(
        '{\n  "name": "my-quant-lab-web",\n  "version": "1.5.15"\n}\n', encoding="utf-8"
    )
    (tmp_path / "frontend" / "package-lock.json").write_text(
        "{\n"
        '  "name": "my-quant-lab-web",\n'
        '  "version": "1.5.15",\n'
        '  "packages": {\n'
        '    "": {\n'
        '      "name": "my-quant-lab-web",\n'
        '      "version": "1.5.15"\n'
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    result = _run_bash("bash scripts/version.sh set v1.6.0", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

    assert (tmp_path / "version.txt").read_text(encoding="utf-8").strip() == "v1.6.0"
    assert "MQL_VERSION=1.6.0" in (tmp_path / ".env.example").read_text(encoding="utf-8")
    assert '__version__ = "1.6.0"' in (tmp_path / "backend" / "app" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert 'version = "1.6.0"' in (tmp_path / "backend" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert (
        json.loads((tmp_path / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
        == "1.6.0"
    )
    lock = json.loads((tmp_path / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == "1.6.0"
    assert lock["packages"][""]["version"] == "1.6.0"


@needs_bash
@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("v1.5.8", "v1.5.9"),
        ("v1.5.9", "v1.6.0"),
        ("v1.5.15", "v1.6.0"),
        ("v1.6.8", "v1.6.9"),
        ("v1.6.9", "v1.7.0"),
        ("v1.9.9", "v2.0.0"),
    ],
)
def test_the_next_version_carries_at_ten(
    tmp_path: pathlib.Path, current: str, expected: str
) -> None:
    """`bump` already carried correctly; this is the table it must keep obeying."""

    script = _text(VERSION_SCRIPT)
    body = script[: script.index('case "${1:-show}" in')]
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "version.sh").write_text(body, encoding="utf-8")
    (tmp_path / "version.txt").write_text(current + "\n", encoding="utf-8")

    result = _run_bash(". scripts/version.sh; bump_version", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith(expected), result.stdout
