"""A release candidate publishes images without publishing a version.

Milestone 1 shipped in `bcea654c3` and had to reach the NAS as a test release
(`v2.5.0-rc.1`) that a user pulls from GHCR exactly like a real one. A release
candidate is not a version: `version.txt` names the version that has *shipped*, and
the scheme's own guard refuses a suffix (`version.sh set v2.5.0-rc.1` exits 2, and
`test_the_released_version_is_a_carried_version` asserts `v\\d+\\.\\d+\\.\\d`), so a
candidate cannot be expressed by moving `version.txt` — that would either fail CI or
require rewriting the scheme (ADR-079).

So the tag event learned about candidates instead, in three places, and all three are
asserted here by running the real steps rather than reading their prose:

* the tag must look like `vX.Y.Z-<label>` and its base must be AHEAD of `version.txt`
  (a candidate names the version it is a candidate for, which is not out yet);
* a candidate never owns `latest` — and the stable tags that do compete must not have
  to outrank it, or `v2.5.0-rc.1` would beat the `v2.5.0` that follows it and freeze
  `MQL_VERSION=latest` on a candidate nobody chose;
* the moving `X.Y` line tag is for released versions only.

The steps are extracted from `release.yml` and executed with `bash`, because the rules
live in shell — a textual search would pass on a step that is spelled correctly and
still takes the wrong branch (ADR-068, ADR-075).
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"
VERSION_FILE = REPO_ROOT / "version.txt"

BASH = shutil.which("bash")
GIT = shutil.which("git")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required to exercise release.yml")
needs_git = pytest.mark.skipif(GIT is None, reason="git is required to choose the highest tag")

# The released version the synthetic workspaces below publish *after* their candidate.
# It is a pinned fixture rather than a read of the real `version.txt`: the candidate has
# to be ahead of the released version, so a moving value would silently rewrite the
# offsets each of those cases asserts (a real release bump must not change them).
RELEASED = "v2.4.4"
CANDIDATE = "v2.5.0-rc.1"

# Steps sit six spaces deep under `jobs.<id>.steps`; a step ends where the next begins.
_STEP = re.compile(r"^ {6}- name: (?P<name>.+)$", re.MULTILINE)


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _step(fragment: str) -> str:
    text = _text(RELEASE)
    matches = list(_STEP.finditer(text))
    for index, match in enumerate(matches):
        if fragment not in match.group("name").strip().strip('"').strip("'"):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[match.start() : end]
    raise AssertionError(f"no step whose name contains {fragment!r}")


def _body(fragment: str) -> str:
    """The dedented `run: |` block of a step, ready to hand to bash."""

    lines = _step(fragment).splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "run: |")
    block = lines[start + 1 :]
    indents = [len(line) - len(line.lstrip()) for line in block if line.strip()]
    assert indents, "the step's run block is empty"
    indent = min(indents)
    return "\n".join(line[indent:] if line.strip() else "" for line in block) + "\n"


def _substitute(body: str, version: str, prerelease: str) -> str:
    """GitHub expressions the step would have resolved before bash saw it."""

    return (
        body.replace("${{ steps.version.outputs.prerelease }}", prerelease)
        .replace("${{ steps.version.outputs.version }}", version)
        .replace("${{ github.event_name }}", "push")
        .replace("${{ inputs.version }}", version)
    )


def _run(
    fragment: str,
    *,
    cwd: pathlib.Path,
    version: str,
    prerelease: str = "false",
    env_extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str, dict[str, str]]:
    """Run one step in `cwd`; return (result, its stdout, the outputs it published)."""

    assert BASH is not None
    ledger = cwd / "github_output"
    ledger.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GITHUB_REF": f"refs/tags/{version}",
            "GITHUB_REF_NAME": version,
            "GITHUB_OUTPUT": str(ledger),
            "GITHUB_REPOSITORY": "bobvane/My-Quant-Lab",
        }
    )
    env.update(env_extra or {})
    result = subprocess.run(
        [BASH, "-c", _substitute(_body(fragment), version, prerelease)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    written: dict[str, str] = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            written[key] = value
    return result, result.stdout, written


def _candidate_workspace(tmp_path: pathlib.Path, version: str = RELEASED) -> pathlib.Path:
    (tmp_path / "version.txt").write_text(f"{version}\n", encoding="utf-8")
    return tmp_path


TAG_STEP = "The tag must agree with version.txt"
LATEST_STEP = "Decide whether this release owns the latest tag"
RESOLVE_STEP = "Resolve version"


@needs_bash
def test_a_candidate_is_flagged_and_leaves_version_txt_alone(tmp_path: pathlib.Path) -> None:
    """The one thing a candidate must not do is pretend to be the released version."""

    workspace = _candidate_workspace(tmp_path)
    _, _, candidate = _run(RESOLVE_STEP, cwd=workspace, version=CANDIDATE)
    assert candidate.get("prerelease") == "true", (
        "a `-rc.N` tag was not recognised as a pre-release"
    )
    _, _, released = _run(RESOLVE_STEP, cwd=workspace, version=RELEASED)
    assert released.get("prerelease") == "false", "a released tag was reported as a pre-release"

    result, stdout, _ = _run(TAG_STEP, cwd=workspace, version=CANDIDATE)
    assert result.returncode == 0, result.stderr
    assert "pre-release" in stdout and RELEASED.lstrip("v") in stdout
    assert (workspace / "version.txt").read_text(encoding="utf-8").strip() == RELEASED, (
        "publishing a candidate rewrote version.txt"
    )


@needs_bash
def test_a_released_tag_still_must_equal_version_txt(tmp_path: pathlib.Path) -> None:
    """The rule a candidate was added *around* must not have been loosened."""

    workspace = _candidate_workspace(tmp_path)
    assert _run(TAG_STEP, cwd=workspace, version=RELEASED)[0].returncode == 0
    mismatch = _run(TAG_STEP, cwd=workspace, version="v2.4.3")[0]
    assert mismatch.returncode != 0, "a tag that disagrees with version.txt was accepted"
    assert "version.txt says" in mismatch.stderr


@needs_bash
@pytest.mark.parametrize(
    ("version", "why"),
    [
        ("v2.4.4-rc.1", "names the version that is already out"),
        ("v2.4.3-rc.2", "names a version behind version.txt"),
        ("v2.5-rc.1", "is not a version at all"),
        ("v2.5.0_rc.1", "is not a version at all"),
    ],
)
def test_a_candidate_must_name_the_version_it_is_a_candidate_for(
    tmp_path: pathlib.Path, version: str, why: str
) -> None:
    workspace = _candidate_workspace(tmp_path)
    result = _run(TAG_STEP, cwd=workspace, version=version)[0]
    assert result.returncode != 0, f"'{version}' ({why}) was accepted as a candidate"


@needs_bash
@needs_git
def test_a_candidate_never_owns_latest_and_does_not_block_the_release_after_it(
    tmp_path: pathlib.Path,
) -> None:
    """`latest` follows released versions — including past a candidate that outranks them."""

    assert GIT is not None
    workspace = _candidate_workspace(tmp_path)
    for command in (
        [GIT, "init", "-q"],
        [GIT, "config", "user.email", "release@example.invalid"],
        [GIT, "config", "user.name", "release"],
        [GIT, "commit", "-q", "--allow-empty", "-m", "release"],
        [GIT, "tag", RELEASED],
        [GIT, "tag", "v2.5.0"],
        [GIT, "tag", CANDIDATE],
        [GIT, "tag", "v2.6.0-rc.1"],
    ):
        done = subprocess.run(command, cwd=str(workspace), capture_output=True, text=True)
        assert done.returncode == 0, f"{command} failed: {done.stderr}"

    # (a) The candidate is the newest tag in the repository — that is what tagging a
    # candidate at the tip of `main` looks like — and it still must not claim `latest`.
    # A plain version sort would hand it over: v2.6.0-rc.1 outranks v2.5.0.
    _, _, candidate_flag = _run(RESOLVE_STEP, cwd=workspace, version="v2.6.0-rc.1")
    candidate, _, candidate_outputs = _run(
        LATEST_STEP, cwd=workspace, version="v2.6.0-rc.1", prerelease=candidate_flag["prerelease"]
    )
    assert candidate.returncode == 0, candidate.stderr
    assert candidate_outputs.get("tag_latest") == "false", "a candidate claimed `latest`"

    # (b) v2.5.0 is the release an earlier candidate was a candidate for. The candidate
    # is still sitting in the repository, ahead of it in a version sort, and must not
    # keep `latest` away from the release that follows it.
    _, _, stable_flag = _run(RESOLVE_STEP, cwd=workspace, version="v2.5.0")
    stable, _, stable_outputs = _run(
        LATEST_STEP, cwd=workspace, version="v2.5.0", prerelease=stable_flag["prerelease"]
    )
    assert stable.returncode == 0, stable.stderr
    assert stable_outputs.get("tag_latest") == "true", (
        "a candidate outranked the release that follows it, so `latest` would freeze on it"
    )


def _stub_docker(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A fake `docker` on PATH that records its arguments instead of talking to a daemon.

    The promotion step's decision -- which tags it would move -- is what has to be
    right, and that decision is shell. Stubbing the client lets the real step run and
    the log say what it would have promoted, without a registry.
    """

    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    stub = bin_dir / "docker"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log.as_posix()}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir, log


def _init_git_tags(directory: pathlib.Path, *tags: str) -> None:
    """A git checkout with the given tags -- what the promotion step's re-check reads."""

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available on this host")
    env = dict(os.environ)
    # pytest's tmp dir sits under a checkout of this repo; keep git from walking
    # up and touching the parent repository's refs while it builds its fixture.
    env["GIT_CEILING_DIRECTORIES"] = str(directory.parent)
    env["GIT_DIR"] = str(directory / ".git")
    env["GIT_WORK_TREE"] = str(directory)
    subprocess.run(
        [git, "init", "-q"],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            git,
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
    )
    # `git tag` takes one name per call; anchor every fixture tag at the empty commit.
    for tag in tags:
        subprocess.run(
            [git, "tag", tag, "HEAD"],
            cwd=directory,
            env=env,
            check=True,
            capture_output=True,
        )


@needs_bash
def test_the_line_tag_is_moved_only_by_a_released_version(tmp_path: pathlib.Path) -> None:
    """`2.5` is a pin for a release line; a candidate must not move it.

    The gate used to ride on `docker/metadata-action`'s `enable=` expression. It now
    lives in the promotion step, which runs only after the smoke test passed, so the
    rule is asserted by RUNNING that step with a stubbed `docker` and reading back the
    tags it asked for -- the same way the rest of this file exercises the release
    rules rather than reading their prose (WP-C-2).
    """

    text = _text(RELEASE)
    assert "type=semver,pattern={{major}}.{{minor}}\n" not in text, (
        "an ungated `X.Y` tag line is left, so a candidate would move the release line"
    )
    app_meta = _step("App metadata")
    assert "{{major}}.{{minor}}" not in app_meta, (
        "the candidate build still claims the `X.Y` line tag before it is tested"
    )

    bin_dir, log = _stub_docker(tmp_path)
    path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    # The re-check inside the step reads the tag ordering `git` reports; a bare
    # temp dir reports nothing, so plant the highest stable tag it compares against.
    _init_git_tags(tmp_path, "v2.4.9", "v2.5.0")

    def promoted(prerelease: str, tag_latest: str) -> list[str]:
        log.write_text("", encoding="utf-8")
        result, _, _ = _run(
            "Promote the verified image",
            cwd=tmp_path,
            version="v2.5.0",
            env_extra={
                "PATH": path,
                "APP_IMAGE": "ghcr.io/bobvane/my-quant-lab-app",
                "VERSION": "v2.5.0",
                "PRERELEASE": prerelease,
                "TAG_LATEST": tag_latest,
            },
        )
        assert result.returncode == 0, result.stderr
        # Each stub call is `buildx imagetools create --tag <image>:<tag> <image>:<ver>`;
        # only the token after `--tag` is a tag this step decided to move.
        targets: list[str] = []
        for line in log.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            for index, part in enumerate(parts):
                if part == "--tag" and index + 1 < len(parts):
                    targets.append(parts[index + 1])
        return targets

    image = "ghcr.io/bobvane/my-quant-lab-app"

    # A candidate moves nothing: not the `X.Y` line, and never `latest`.
    assert promoted("true", "false") == [], "a candidate promoted a moving tag"

    # A released version that owns `latest` moves both.
    both = promoted("false", "true")
    assert sorted(both) == sorted([f"{image}:2.5", f"{image}:latest"]), (
        f"a released version did not promote both moving tags: {both}"
    )

    # A released version that does NOT own `latest` must leave `latest` alone.
    only_line = promoted("false", "false")
    assert only_line == [f"{image}:2.5"], (
        f"a release that does not own `latest` did not promote exactly the line tag: {only_line}"
    )

    # An older version must not be able to overwrite `latest` after a newer one was
    # published while it was still building: the in-step re-check reads the tag
    # ordering that `git` reports at the moment it runs.
    _init_git_tags(tmp_path, "v2.5.1")
    outworn = promoted("false", "true")
    assert outworn == [f"{image}:2.5"], (
        f"an older release promoted `latest` on top of a newer tag: {outworn}"
    )


def test_a_candidate_is_published_as_a_pre_release_and_never_rewrites_the_version() -> None:
    """GitHub must not report a candidate as the newest release, and nothing writes back."""

    text = _text(RELEASE)
    assert "prerelease: ${{ contains(steps.version.outputs.version, '-') }}" in text, (
        "a candidate would be published as a full release"
    )
    assert "> version.txt" not in text and ">> version.txt" not in text, (
        "the release pipeline writes version.txt; that file is moved by scripts/version.sh"
    )
    # The invariant the candidate flow depends on: `version.txt` names a *released*
    # version (no suffix) and nothing in the pipeline writes it — `scripts/version.sh`
    # is the only writer, and it never writes a candidate. Asserting the exact string
    # would turn every release into a failing guard, so the shape is what is checked.
    released = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"v\d+\.\d+\.\d", released), (
        f"version.txt names {released!r}, which is not a released version; a candidate is "
        "published without moving that file, so it must still name the release it follows"
    )
