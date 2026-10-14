"""Guards for the release pipeline: publishing is not a substitute for a verdict.

`release.yml` builds and pushes the backend and web images, then boots the
stock production compose file to smoke test exactly what it published. On the v1.5.2
release that smoke test said `SMOKE_TEST_FAILED` — the images could not boot, because
the migration id did not fit `alembic_version.version_num` (ADR-064) — and the run
still concluded `success`: the step carried `continue-on-error: true` (release run
`37084889121`, step "Smoke test the released images" reported `completed/success`).
The GitHub Release was created all the same, and its notes told the reader to run
`docker compose pull && docker compose up -d`. The only record that said "do not
deploy" was a line of console text in a log nobody had a reason to open.

The same step only ever asked the API container (`127.0.0.1:8080`): `docker compose
up -d` returns 0 even when the App crash loops, so a release whose UI never
came up produced `SMOKE_TEST_OK` too.

The step itself no longer knows how to answer that question: the verdict — the API
answers, the web edge serves the application, a missing hashed asset is a 404 — lives
in `scripts/verify-stack.sh`, because the nightly pipeline asks the same question of
the images it publishes, and two copies of a verdict drift (ADR-070, ADR-076).

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
VERIFY_STACK = REPO_ROOT / "scripts" / "verify-stack.sh"

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


def test_the_release_smoke_test_asks_the_shared_verdict():
    """One release publishes three images; one script decides whether they served."""

    body = _uncommented(_step(_text(RELEASE), "Smoke test the released images"))
    assert "bash scripts/verify-stack.sh" in body, (
        "the smoke test no longer asks the shared verdict"
    )
    assert "verify_rc" in body, "the shared verdict's exit status does not reach this step's status"
    # ... and the failure exit must be the one that consults it.
    before_failure = body.split("SMOKE_TEST_FAILED")[0]
    assert "verify_rc" in before_failure[-400:], "the failure exit ignores the shared verdict"


def test_the_shared_verdict_asks_the_web_container_too():
    """The serving half is asserted where the verdict lives, not merely fetched."""

    script = _uncommented(_text(VERIFY_STACK))
    assert "8080" in script, "the verdict no longer asks the API"
    assert "8081" in script, "the verdict never asks the web edge"
    assert "web_ok" in script, "the web half contributes nothing to the verdict"
    # The web half must assert, not merely fetch: liveness, the app's mount point, and
    # the edge contract ADR-068 made real (a missing asset is a 404, not the shell).
    assert "/healthz" in script
    assert 'id="app"' in script
    assert "/assets/" in script and "404" in script
    assert "exit 1" in script and "VERIFY_STACK_FAILED" in script, "the verdict cannot fail"


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


def test_the_release_notes_carry_the_smoke_verdict() -> None:
    """Publishing on a failed smoke test is deliberate; publishing *silently* is not.

    The notes step runs `if: always()`, so the release page is exactly where a reader
    who never opens the run log learns whether the images were seen to boot. It used to
    say nothing about it, and advertised `docker compose pull && docker compose up -d`
    either way (ADR-102).
    """

    text = _text(RELEASE)
    notes = _step(text, "Generate release notes")
    assert "steps.smoke.outcome" in notes, "the notes never learn what the smoke test said"
    assert "Smoke test:" in notes, "the verdict is read but not written into the notes"
    # Both branches must exist: a note that only ever says "passed" is the old silence.
    assert "passed" in notes and "not run" in notes, "the failure branch is missing"


def test_the_release_smoke_test_pulls_the_version_it_is_releasing():
    """Smoke testing `latest` would certify whatever happened to be there."""

    body = _step(_text(RELEASE), "Smoke test the released images")
    assert '"${APP_IMAGE}:${VERSION}"' in body, "the app image is not pulled at the released tag"


# --------------------------------------------------------------------------- #
# WP-C-2: the moving tags move only after the smoke test proved the image boots
# --------------------------------------------------------------------------- #
def test_the_candidate_is_published_under_tags_that_name_this_build_only() -> None:
    """`latest` and `2.7` are pointers a deployment follows, not build identities.

    They used to be pushed by the same step that builds the image -- before the
    smoke test runs -- so a build whose stack never came up still became what
    `MQL_VERSION=latest` (the documented default) resolved to. The candidate now
    carries only `vX.Y.Z` / `X.Y.Z`; the moving tags are promoted afterwards.
    """

    body = _uncommented(_step(_text(RELEASE), "App metadata"))
    assert "type=ref,event=tag" in body, "the immutable tag reference is gone"
    assert "pattern={{version}}" in body, "the immutable version tag is gone"
    assert "latest" not in body, "the candidate still pushes `latest` before it is tested"
    assert "major}}.{{minor" not in body, "the candidate still moves the `X.Y` release line"


def test_the_moving_tags_are_promoted_only_after_a_successful_smoke_test() -> None:
    text = _text(RELEASE)
    body = _step(text, "Promote the verified image")
    assert "if: steps.smoke.outcome == 'success'" in body, (
        "the moving tags are promoted without the smoke verdict, so a failed build "
        "still becomes the default deployment target"
    )
    # Order: the image is built, then booted, then promoted -- never before.
    assert text.index("Build and push app image") < text.index("Smoke test the released images")
    assert text.index("Smoke test the released images") < text.index("Promote the verified image")


def test_the_promotion_retags_the_digest_that_was_tested() -> None:
    """A rebuild would promote bytes nobody booted; a registry-side retag cannot."""

    body = _step(_text(RELEASE), "Promote the verified image")
    assert "imagetools create" in body, "the promotion rebuilds instead of retagging"
    assert '"${APP_IMAGE}:${version}"' in body, "the promotion does not name the tested image"


def test_the_promotion_respects_the_prerelease_and_latest_gates() -> None:
    body = _step(_text(RELEASE), "Promote the verified image")
    assert "PRERELEASE" in body and 'PRERELEASE}" = "false' in body, (
        "a pre-release could move the `X.Y` release line"
    )
    assert "TAG_LATEST" in body and 'TAG_LATEST}" = "true' in body, (
        "a release that does not own `latest` could move it"
    )
    assert "no moving tag applies" in body, "the no-op path is missing"


def test_the_release_notes_say_whether_the_moving_tags_advanced() -> None:
    """The release is still published on failure (deliberate); silence is not allowed.

    Whether `latest` moved decides what an unpinned `docker compose pull` gets, so
    the page a reader actually opens has to say it.
    """

    notes = _step(_text(RELEASE), "Generate release notes")
    assert "not** moved" in notes or "not moved" in notes, (
        "the notes never say the moving tags were withheld"
    )
    assert "MQL_VERSION=${version}" in notes, "the notes do not say how to try the build anyway"


def test_the_release_notes_report_the_promotion_outcome_not_the_smoke_outcome() -> None:
    """A note that claims the moving tags advanced must be based on the step that
    moves them.

    The smoke test can pass and the promotion still fail (the tag command errors
    out, a registry write fails); a note worded off the smoke verdict would tell
    the reader `latest` moved when it did not.
    """

    notes = _step(_text(RELEASE), "Generate release notes")
    assert "steps.promote.outcome" in notes, "the notes do not read the promotion step's outcome"
    # The success claim must come from the promotion's success, not the smoke's.
    assert 'if [ "${promote}" = "success" ]' in notes, (
        "the notes claim the tags advanced without the promotion step having succeeded"
    )
    # All three states must be reported: promoted; smoke passed but promotion did
    # not; neither.
    assert "did not succeed" in notes, (
        "the notes have no branch for a failed promotion after a passed smoke test"
    )


def test_the_release_rechecks_latest_ownership_before_moving_it() -> None:
    """Releases are not serialised, so `latest` is re-checked at the point it moves.

    Ownership is decided before the build (the highest tag cannot be outranked yet)
    and re-asserted by the promotion step: a newer tag published while this build
    was in flight must not be undone by an older one finishing last.
    """

    promote = _step(_text(RELEASE), "Promote the verified image")
    assert "git tag --list 'v*' --sort=-v:refname" in promote, (
        "the promotion does not re-check the tag ordering it depends on"
    )
    assert "no longer the highest tag" in promote, (
        "a release that lost `latest` ownership is not told it stayed put"
    )
    # And that re-check must be narrower than the release step that decides it: the
    # re-check sits inside the step that moves the tag.
    assert 'moves+=("latest")' in promote
    assert "highest" in promote.split('moves+=("latest")')[0]


def test_the_promotion_step_is_the_only_writer_of_the_moving_tags() -> None:
    """The moving tags have exactly one writer, and it runs after the smoke test.

    `docker buildx imagetools create` retags the registry; anything else that
    pushes a moving tag is a second writer that can interleave with this one and
    undo it. Comments that mention the command are not writers.
    """

    text = _text(RELEASE)
    live_invocations = [
        line
        for line in text.splitlines()
        if "docker buildx imagetools create" in line and not line.lstrip().startswith("#")
    ]
    assert len(live_invocations) == 1, (
        f"the moving tags are written from {len(live_invocations)} live invocation sites; "
        "two independent writers can interleave and overwrite each other"
    )
    assert "if: steps.smoke.outcome == 'success'" in text, (
        "the moving-tag writer is not gated on the smoke test, so a failed build "
        "could still advance the default deployment target"
    )


def test_release_runs_are_serialised_across_versions() -> None:
    """Different versions queue behind each other; none may run two at a time.

    A per-version concurrency group (`release-${{ github.ref }}`) would let two
    releases build and smoke-test side by side, and whichever finished LAST would
    win the write to `latest` -- an older release overwriting a newer one. The
    group must therefore not key on the triggering ref: all release runs share
    one group, and `cancel-in-progress: false` keeps the queue loss-free (a run
    in flight is never replaced by a queued one).

    `queue: max` additionally keeps up to 100 waiting runs in the group instead of
    letting the default queue policy drop runs it considers stale. Queue ordering
    follows when a run entered the waiting state, not strictly by version number
    or trigger time, so this caps loss, it does not reorder by recency.
    """

    text = _text(RELEASE)
    concurrency = [
        line for line in text.splitlines() if "group:" in line and "concurrency" not in line
    ]
    groups = [line.split("group:")[-1].strip() for line in concurrency]
    assert len(groups) == 1, f"multiple concurrency groups declared: {groups}"
    assert "github.ref" not in groups[0], (
        "the concurrency group keys on the ref, so two releases run in parallel; "
        "an older one can then overwrite the newer one's `latest`"
    )
    assert "cancel-in-progress: false" in text, (
        "a queued release would be CANCELLED in favour of a newer one, so a "
        "legitimate release could be lost"
    )
    assert "queue: max" in text, (
        "the group would drop runs it considers stale instead of holding up to 100 waiting runs"
    )
