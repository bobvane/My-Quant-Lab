"""Guards for the release pipeline: publishing is not a substitute for a verdict.

`release.yml` builds and pushes the backend, proxy and web images, then boots the
stock production compose file to smoke test exactly what it published. On the v1.5.2
release that smoke test said `SMOKE_TEST_FAILED` — the images could not boot, because
the migration id did not fit `alembic_version.version_num` (ADR-064) — and the run
still concluded `success`: the step carried `continue-on-error: true` (release run
`37084889121`, step "Smoke test the released images" reported `completed/success`).
The GitHub Release was created all the same, and its notes told the reader to run
`docker compose pull && docker compose up -d`. The only record that said "do not
deploy" was a line of console text in a log nobody had a reason to open.

The same step only ever asked the API container (`127.0.0.1:8080`): `docker compose
up -d` returns 0 even when the web container crash loops, so a release whose UI never
came up produced `SMOKE_TEST_OK` too.

These guards read the workflow text. They can prove that the verdict is not swallowed
and that both containers are interrogated; they cannot prove how a runner behaves
(ADR-068 draws the same line for the nginx config, and ADR-075 for this one).
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"

# Steps sit six spaces deep under `jobs.<id>.steps`; a step ends where the next begins.
_STEP = re.compile(r"^ {6}- name: (?P<name>.+)$", re.MULTILINE)


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


def _uncommented(text: str) -> str:
    """Drop YAML comment lines: these guards read configuration, not prose.

    The workflow explains its own decisions in comments, including the ones these
    guards enforce ("no `continue-on-error` here", "the v1.5.2 release printed
    `SMOKE_TEST_FAILED`"), so a naive substring search would fail on the explanation
    of the rule instead of on a breach of it.
    """

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_release_workflow_is_where_this_guard_expects_it():
    names = [name for name, _ in _steps(_text(RELEASE))]
    assert "Smoke test the released images" in names
    assert "Create GitHub Release" in names


def test_no_workflow_swallows_a_step_failure():
    """A check whose failure cannot fail the run is not a check (ADR-072, ADR-073).

    `continue-on-error` is the one YAML spelling of that silence. Keeping later steps
    alive is what `if: always()` is for, and the release workflow uses it deliberately:
    the images are already published, so the release page is still created while the
    run reports the failure.
    """

    offenders: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(_text(path).splitlines(), start=1):
            if "continue-on-error" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "a workflow step swallows its own failure, so the run can report success while "
        f"the check failed: {offenders}"
    )


def test_the_release_smoke_test_asks_the_web_container_too():
    """One release publishes three images; one verdict must cover the serving half."""

    body = _uncommented(_step(_text(RELEASE), "Smoke test the released images"))
    assert "8080" in body, "the smoke test no longer asks the API"
    assert "8081" in body, "the smoke test never asks the web container"
    assert "web_ok" in body, "the web half contributes nothing to the verdict"
    # The web half must assert, not merely fetch: liveness, the app's mount point, and
    # the edge contract ADR-068 made real (a missing asset is a 404, not the shell).
    assert "/healthz" in body
    assert 'id="app"' in body
    assert "/assets/" in body and "404" in body
    # ... and it must reach the same verdict as the API half.
    before_failure = body.split("SMOKE_TEST_FAILED")[0]
    assert "web_ok" in before_failure[-400:], "the failure exit ignores the web half"


def test_the_release_smoke_test_can_fail_its_step():
    body = _step(_text(RELEASE), "Smoke test the released images")
    assert "SMOKE_TEST_FAILED" in body
    assert "exit 1" in body, "the smoke test cannot fail the step it runs in"
    assert "up_rc" in body, "the compose exit code is not part of the verdict"


def test_the_release_is_still_published_when_the_smoke_test_fails():
    """The lazy fix for a red release run is to stop publishing. That is not the fix.

    The images are already in GHCR by the time the smoke test runs, and the release
    page is where a NAS user gets them; withholding it would change nothing about the
    artifacts and would hide the instructions that say how to pin a version.
    """

    text = _text(RELEASE)
    release_body = _step(text, "Create GitHub Release")
    assert "if: always()" in release_body, "a failing smoke test would skip the release"
    assert text.index("Smoke test the released images") < text.index("Create GitHub Release")


def test_the_release_smoke_test_pulls_the_version_it_is_releasing():
    """Smoke testing `latest` would certify whatever happened to be there."""

    body = _step(_text(RELEASE), "Smoke test the released images")
    for image in ("BACKEND_IMAGE", "WEB_IMAGE", "PROXY_IMAGE"):
        assert f'"${{{image}}}:${{VERSION}}"' in body, f"{image} is not pulled at the released tag"
