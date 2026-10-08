"""Guards for the nightly pipeline: an image nobody starts is an unverified claim.

``nightly.yml`` used to build **two** images (``backend``, ``web``), push them as
``:nightly``, and stop. Three problems, none of them visible from the workflow's own
name (ADR-076):

- the release pipeline published **three** images (``backend``, ``proxy``, ``web``)
  while nightly published two, so the tags were not the same set: ``MQL_VERSION=nightly``
  could not start the stock compose file at all (v2.6.0 retired the proxy image, so the
  two pipelines again publish the same set — the guard below holds that equality);
- both build steps read `cache-from: type=gha` and neither wrote one, so the nightly
  run borrowed a cache it never contributed to;
- nothing ever started the images. A nightly that cannot boot would be discovered by
  whoever pulled it, which is the definition of a claim with no verdict (ADR-072,
  ADR-073, ADR-075 all draw this same line).

These guards read the workflow and script text: they can prove the set is complete and
that a verdict is wired in, not that a runner boots anything.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
NIGHTLY = WORKFLOWS / "nightly.yml"
RELEASE = WORKFLOWS / "release.yml"
VERIFY_STACK = REPO_ROOT / "scripts" / "verify-stack.sh"

# Steps sit six spaces deep under `jobs.<id>.steps`; a step ends where the next begins.
_STEP = re.compile(r"^ {6}- name: (?P<name>.+)$", re.MULTILINE)
# Job ids sit two spaces deep under `jobs:`.
_JOB = re.compile(r"^ {2}(?P<name>[a-z][a-z0-9_-]*):$", re.MULTILINE)
# The two spellings of "which Dockerfile, which cache" this pipeline uses.
_DOCKERFILE = re.compile(r"file: (docker/Dockerfile\.[a-z]+)")
_NIGHTLY_TAG = re.compile(r"tags: \$\{\{ env\.(?P<image>[A-Z_]+) \}\}:nightly")
_IMAGE_CONSTANT = re.compile(r"^ {2}(?P<name>[A-Z_]+_IMAGE):", re.MULTILINE)


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _steps(text: str) -> list[tuple[str, str]]:
    matches = list(_STEP.finditer(text))
    steps: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        name = match.group("name").strip().strip('"').strip("'")
        steps.append((name, text[match.start() : end]))
    return steps


def _step(text: str, fragment: str) -> str:
    for name, body in _steps(text):
        if fragment in name:
            return body
    raise AssertionError(f"no step whose name contains {fragment!r}")


def _jobs(text: str) -> list[tuple[str, str]]:
    matches = list(_JOB.finditer(text))
    jobs: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        jobs.append((match.group("name"), text[match.start() : end]))
    return jobs


def _job(text: str, name: str) -> str:
    for job_name, body in _jobs(text):
        if job_name == name:
            return body
    raise AssertionError(f"no job named {name!r}; found {[job for job, _ in _jobs(text)]}")


def _uncommented(text: str) -> str:
    """Drop YAML comment lines: these guards read configuration, not prose."""

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_nightly_workflow_is_where_this_guard_expects_it():
    jobs = [name for name, _ in _jobs(_text(NIGHTLY))]
    assert "images" in jobs, "the nightly pipeline stopped rebuilding images"
    assert "verify" in jobs, "the nightly pipeline publishes images nobody starts"
    assert _text(VERIFY_STACK).strip(), "the shared verdict has no content"


def test_the_nightly_images_are_the_images_the_release_publishes():
    """A tag has to be a complete set: the compose file has to start from it alone."""

    nightly = _text(NIGHTLY)
    release = _text(RELEASE)
    nightly_files = set(_DOCKERFILE.findall(nightly))
    release_files = set(_DOCKERFILE.findall(release))
    assert nightly_files == release_files, (
        "the nightly and release pipelines build different image sets, so `:nightly` is "
        f"not a usable tag: nightly={sorted(nightly_files)} release={sorted(release_files)}"
    )
    assert nightly_files == {
        "docker/Dockerfile.backend",
        "docker/Dockerfile.web",
    }

    built = {match.group("image") for match in _NIGHTLY_TAG.finditer(nightly)}
    declared = set(_IMAGE_CONSTANT.findall(nightly))
    assert built == {"BACKEND_IMAGE", "WEB_IMAGE"}, f"nightly tags {sorted(built)}"
    assert built <= declared, f"nightly tags {sorted(built - declared)} an undeclared image name"
    release_declared = set(_IMAGE_CONSTANT.findall(release))
    assert built == release_declared, (
        f"nightly publishes {sorted(built & release_declared or built)} but release declares "
        f"{sorted(release_declared)}"
    )


def test_every_gha_cache_read_has_a_gha_cache_write():
    """`cache-from` with no `cache-to` in the same file is a one-way claim."""

    offenders: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = _uncommented(_text(path))
        reads = text.count("cache-from: type=gha")
        writes = text.count("cache-to: type=gha")
        if reads != writes:
            offenders.append(f"{path.name}: {reads} cache-from, {writes} cache-to")
    assert not offenders, f"a workflow reads a build cache it never writes: {offenders}"


def test_the_nightly_verification_boots_the_images_it_publishes():
    """The verify job has to start the stack from the registry, at the nightly tag."""

    text = _text(NIGHTLY)
    body = _uncommented(_job(text, "verify"))
    assert "needs: images" in body, "verification does not wait for the images it verifies"
    assert "VERSION: nightly" in body, "the verification does not pin the nightly tag"
    for image in ("BACKEND_IMAGE", "WEB_IMAGE"):
        assert f'"${{{image}}}:${{VERSION}}"' in body, f"{image} is not pulled at the nightly tag"
    assert "docker compose --env-file .env.example up -d" in body
    assert "bash scripts/verify-stack.sh" in body, (
        "the verification does not ask the shared verdict"
    )
    assert "down -v" in body, "the verification leaves its stack behind"
    assert "NIGHTLY_VERIFY_FAILED" in body and "exit 1" in body, "the verification cannot fail"
    before_failure = body.split("NIGHTLY_VERIFY_FAILED")[0]
    assert "verify_rc" in before_failure[-400:], "the failure exit ignores the shared verdict"


def test_both_pipelines_ask_the_same_verdict():
    """Two copies of a verdict drift; the nightly one did not exist at all (ADR-070)."""

    release_body = _uncommented(_step(_text(RELEASE), "Smoke test the released images"))
    nightly_body = _uncommented(_job(_text(NIGHTLY), "verify"))
    for name, body in (("release.yml", release_body), ("nightly.yml", nightly_body)):
        assert "scripts/verify-stack.sh" in body, f"{name} decides the stack's fate on its own"


def test_the_shared_verdict_is_runnable_shell():
    """A verdict script with CRLF line endings cannot run under bash at all."""

    raw = VERIFY_STACK.read_bytes()
    assert b"\r" not in raw, "scripts/verify-stack.sh has CRLF line endings"
    text = raw.decode("utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    for marker in ("VERIFY_STACK_OK", "VERIFY_STACK_FAILED", "exit 1", "api_ok", "web_ok"):
        assert marker in text, f"the shared verdict lost {marker!r}"
