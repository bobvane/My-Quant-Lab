"""The published example secret must not protect a production deployment.

`.env.example` ships `SECRET_KEY=change-me-openssl-rand-hex-32`, and
`docker-compose.yml` only asks that the variable be *set*, which the example
value satisfies. The at-rest encryption key for stored provider credentials is
derived from that value (`app.infrastructure.secrets`), so a deployment that
keeps it hands anyone with a copy of this repository -- or of the database --
the keys inside it (ADR-077).

The same reasoning applies to the pipelines that boot the stock compose file:
they must generate a secret, not carry a published one, because the stock file
puts the API in production.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"
COMPOSE = REPO_ROOT / "docker-compose.yml"
README = REPO_ROOT / "README.md"
GENERATED = 'export SECRET_KEY="$(openssl rand -hex 32)"'


def _example_value(key: str) -> str:
    """Read a value straight out of the shipped example file."""

    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{key} is not in .env.example")


def _production_settings(monkeypatch: pytest.MonkeyPatch, secret: str) -> Settings:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", secret)
    return Settings(_env_file=None)


def test_the_shipped_example_secret_is_refused_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever `.env.example` ships today must not be accepted tomorrow."""

    example = _example_value("SECRET_KEY")
    assert example, "the example file must name a placeholder to replace"
    with pytest.raises(ValidationError) as caught:
        _production_settings(monkeypatch, example)
    assert "SECRET_KEY" in str(caught.value)


def test_a_short_secret_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as caught:
        _production_settings(monkeypatch, "abc123")
    assert "at least" in str(caught.value)


def test_an_empty_secret_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as caught:
        _production_settings(monkeypatch, "   ")
    assert "empty" in str(caught.value)


def test_a_generated_secret_is_accepted_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _production_settings(monkeypatch, "9f" * 16)
    assert settings.is_production is True
    assert settings.secret_key == "9f" * 16


def test_a_random_secret_may_start_with_the_published_digits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Published values are compared exactly: only the value itself is refusable.

    A substring rule would have refused this generated secret -- and the test
    suite's own conftest secret -- for no security benefit, so the published
    literals are matched as literals (ADR-077).
    """

    generated = "0123456789abcdef" + "9f" * 8
    settings = _production_settings(monkeypatch, generated)
    assert settings.secret_key == generated


def test_local_development_keeps_working_with_the_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local runs must not need a secret to be generated first."""

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    monkeypatch.setenv("SECRET_KEY", _example_value("SECRET_KEY"))
    assert Settings(_env_file=None).secret_key.startswith("change-me")


def test_the_refusal_names_the_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as caught:
        _production_settings(monkeypatch, _example_value("SECRET_KEY"))
    assert "openssl rand -hex 32" in str(caught.value)


def _role_branch(text: str, role: str) -> str:
    """Return the body of one `case "$ROLE"` branch."""

    start = text.index(f"\n    {role})\n")
    end = text.index("\n        ;;", start)
    return text[start:end]


def test_the_entrypoint_reports_configuration_before_it_waits_for_the_database() -> None:
    """A refused secret must not look like a database that never came up."""

    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "check_settings()" in text
    assert "from app.core.config import settings" in text
    assert "refusing to start" in text
    for role in ("api", "worker", "scheduler", "migrate"):
        branch = _role_branch(text, role)
        assert "check_settings || exit 1" in branch, f"{role} does not check its settings"
        assert branch.index("check_settings || exit 1") < branch.index("wait_for_db"), (
            f"{role} waits for the database before checking its settings"
        )


@pytest.mark.parametrize("workflow", ["release.yml", "nightly.yml"])
def test_the_pipelines_that_boot_the_stack_generate_a_secret(workflow: str) -> None:
    text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert ".env.example" in text, "this test assumes the pipeline uses the stock file"
    assert "up -d" in text
    assert GENERATED in text, f"{workflow} boots the stock stack without generating a secret"
    assert "ci-smoke-secret-key" not in text, f"{workflow} still carries a published secret"


def test_the_compose_file_still_refuses_to_start_without_a_secret() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "${SECRET_KEY:?SECRET_KEY is required}" in text


def test_a_secret_this_repository_published_is_refused_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The README used to hand every reader this exact value."""

    with pytest.raises(ValidationError):
        _production_settings(monkeypatch, "0123456789abcdef0123456789abcdef")


def test_no_documented_secret_can_protect_a_production_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever the setup docs show must not be a working production secret.

    A generated value or a shell substitution is fine (it is not usable as-is,
    and the deployment that runs it produces its own); a literal that Settings
    would accept is a published key, which is the whole point of ADR-077.
    """

    documented: list[tuple[str, str]] = []
    for path in (README, ENV_EXAMPLE):
        for match in re.finditer(r"SECRET_KEY=(\S+)", path.read_text(encoding="utf-8")):
            documented.append((path.name, match.group(1).strip("'\"`")))
    assert len(documented) >= 2, "the setup docs must show how SECRET_KEY is set"

    usable = []
    for where, value in documented:
        try:
            _production_settings(monkeypatch, value)
        except ValidationError:
            continue
        usable.append(f"{where}: {value}")
    assert not usable, "documented secrets a production deployment would accept: " + "; ".join(
        usable
    )
