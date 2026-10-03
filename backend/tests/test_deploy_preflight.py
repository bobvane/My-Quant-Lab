"""A deployment check must cover the deployment it claims to check.

`scripts/preflight.sh` hardcoded two image names -- backend and web -- so the
`my-quant-lab-docker-proxy` image was never mentioned, even though the stock
compose file starts it (ADR-076 forgot the same image in the nightly pipeline).
It also demanded source files (`docker/Dockerfile.backend`, ...) that a
documented two-file NAS deployment does not have, and nothing ran it at all: its
verdict lived only in whoever's terminal (ADR-078).

These guards hold the three fixes in place -- the image list is derived from the
compose file, source files are required only when that file builds from source,
and the secret rule is exactly the rule the API enforces -- plus the wiring that
makes the verdict matter: CI runs the script inside the step that boots the
stack, before `up -d`.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import shutil
import subprocess

import pytest
import yaml

from app.core.config import _PUBLISHED_SECRETS, _SECRET_MIN_LENGTH, _SECRET_PLACEHOLDERS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
OVERLAY = REPO_ROOT / "docker-compose.build.yml"
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.sh"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required to exercise the script")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _services(path: pathlib.Path) -> dict[str, dict]:
    """Compose services with their `<<: *anchor` merges resolved.

    PyYAML keeps a merge key as the literal key `<<`, so a service that inherits
    its image from an anchor would otherwise look like it had none.
    """

    data = yaml.safe_load(_text(path)) or {}
    resolved: dict[str, dict] = {}
    for name, body in (data.get("services") or {}).items():
        service = dict(body or {})
        merged = service.pop("<<", None)
        if isinstance(merged, dict):
            merged = dict(merged)
            merged.update(service)
            service = merged
        resolved[str(name)] = service
    return resolved


def _env_example_value(key: str) -> str:
    for line in _text(ENV_EXAMPLE).splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{key} is not in .env.example")


def _project_images() -> dict[str, str]:
    """service name -> image, for every service whose image is ours."""

    version = _env_example_value("MQL_VERSION")
    images: dict[str, str] = {}
    for name, service in _services(COMPOSE).items():
        image = str(service.get("image", ""))
        image = image.replace("${MQL_VERSION:-latest}", version).replace("${MQL_VERSION}", version)
        if "my-quant-lab" in image:
            images[name] = image
    return images


def _bash_function(name: str) -> str:
    """The text of one `name() { ... }` definition from the preflight script."""

    text = _text(PREFLIGHT)
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start)
    return text[start : end + 2]


def _run_bash(
    snippet: str,
    *,
    cwd: pathlib.Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        [BASH, "-c", snippet],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# --- the deployment file itself ------------------------------------------------


def test_the_deployment_compose_file_never_builds_from_source() -> None:
    """Two files must stay enough: a build: section asks for a checkout."""

    for name, service in _services(COMPOSE).items():
        assert "build" not in service, (
            f"{name} builds from source in the deployment file; the NAS deploy "
            "ships only docker-compose.yml + .env (ADR-078)"
        )


def test_the_overlay_builds_every_image_the_deployment_pulls() -> None:
    """CI builds these images; a service the overlay forgets cannot be built."""

    overlay = _services(OVERLAY)
    project = _project_images()
    assert len(project) >= 3, (
        "the deployment is expected to pull the backend, the web and the proxy image, "
        f"found {sorted(project)}"
    )
    assert set(overlay) <= set(_services(COMPOSE)), (
        "the overlay names a service the deployment file does not: "
        f"{sorted(set(overlay) - set(_services(COMPOSE)))}"
    )
    for name in project:
        build = overlay.get(name, {}).get("build")
        assert build, f"{name} is pulled from the registry but has no build recipe for CI"
        dockerfile = REPO_ROOT / str(build["dockerfile"])
        assert dockerfile.is_file(), f"{name} builds from a missing {build['dockerfile']}"


def test_the_proxy_image_is_one_the_deployment_pulls() -> None:
    """The image ADR-076 forgot is named by the deployment file, not a list."""

    assert any("docker-proxy" in image for image in _project_images().values())


# --- the check itself ----------------------------------------------------------


def test_the_preflight_derives_its_image_list_from_the_compose_file() -> None:
    text = _text(PREFLIGHT)
    assert "ghcr.io/bobvane/my-quant-lab" not in text, "the image list is hardcoded again"
    assert re.search(r"image:\[\[:space:\]\]\*", text), "preflight does not read image: lines"
    assert "my-quant-lab" in text, "preflight does not filter for this project's images"


def test_the_preflight_keeps_the_api_secret_rule() -> None:
    """A rule that differs from the API's rule is not a rule (ADR-077)."""

    text = _text(PREFLIGHT)
    placeholders = _bash_list(text, "SECRET_PLACEHOLDERS")
    published = _bash_list(text, "PUBLISHED_SECRETS")
    assert placeholders == list(_SECRET_PLACEHOLDERS)
    assert published == list(_PUBLISHED_SECRETS)
    assert f"SECRET_MIN_LENGTH={_SECRET_MIN_LENGTH}" in text


def _bash_list(text: str, name: str) -> list[str]:
    match = re.search(rf"^{name}=\((.*?)\)$", text, re.MULTILINE)
    assert match, f"{name} is not defined in preflight.sh"
    return shlex.split(match.group(1))


def test_the_preflight_only_demands_source_files_when_it_builds_from_source() -> None:
    """A two-file deploy has no Dockerfile, and that is not a failure."""

    text = _text(PREFLIGHT)
    assert "compose_builds_from_source" in text
    guard = text.index("if compose_builds_from_source; then")
    demanded = text.index("this compose file builds from source")
    assert guard < demanded, "source files are demanded even without a build: section"
    assert "two-file deploy needs no source" in text


def test_the_preflight_prefers_the_shell_environment_over_the_env_file() -> None:
    """Compose resolves an exported variable first; the check must agree."""

    text = _text(PREFLIGHT)
    assert text.index('printenv "$key"') < text.index('grep -E "^[[:space:]]*${key}="')


def test_the_preflight_checks_a_local_image_before_the_registry() -> None:
    """CI builds its own images and must not need the registry to answer."""

    text = _text(PREFLIGHT)
    assert text.index("docker image inspect") < text.index("docker manifest inspect")


def test_the_preflight_reports_a_failed_check_and_a_usage_error() -> None:
    text = _text(PREFLIGHT)
    assert "Pre-flight FAILED" in text
    assert "exit 1" in text
    assert "unknown argument" in text
    assert "exit 2" in text


def test_ci_runs_the_check_inside_the_step_that_boots_the_stack() -> None:
    """A verdict nobody runs changes nothing; it must gate `up -d`."""

    text = _text(CI)
    start = text.index("- name: Boot the stack")
    rest = text[start:]
    end = rest.find("\n      - name:")
    boot = rest if end == -1 else rest[:end]
    assert "preflight.sh" in boot, "CI boots the stack without checking the deployment"
    assert boot.index("preflight.sh") < boot.index("up -d"), "the check runs after the boot"
    assert "exit 1" in boot, "a refused pre-flight does not fail the job"
    assert text.count("preflight.sh") == 1, "the check belongs in the booting step"


# --- and it behaves --------------------------------------------------------------


@needs_bash
def test_the_preflight_lists_exactly_the_images_the_deployment_pulls() -> None:
    """The derivation, run for real against the real compose file."""

    snippet = "\n".join(
        [
            'COMPOSE_PATH="docker-compose.yml"',
            'ENV_PATH=".env.example"',
            _bash_function("env_value"),
            _bash_function("expand_image"),
            _bash_function("compose_images"),
            "compose_images",
        ]
    )
    result = _run_bash(snippet)
    assert result.returncode == 0, result.stderr
    listed = result.stdout.split()
    assert sorted(set(listed)) == sorted(set(_project_images().values()))
    assert len(listed) == len(set(listed)), (
        "the same project image is listed more than once: the compose file should "
        "name it where it is defined, not in every service that inherits it"
    )


@needs_bash
def test_the_shell_value_beats_the_env_file_value_in_practice() -> None:
    snippet = "\n".join(
        [
            'ENV_PATH=".env.example"',
            _bash_function("env_value"),
            'printf "%s" "$(env_value POSTGRES_PASSWORD)"',
        ]
    )
    exported = _run_bash(snippet, extra_env={"POSTGRES_PASSWORD": "exported-wins"})
    assert exported.stdout == "exported-wins", exported.stdout
    from_file = _run_bash(snippet, extra_env={"POSTGRES_PASSWORD": ""})
    assert from_file.stdout == _env_example_value("POSTGRES_PASSWORD"), from_file.stdout
