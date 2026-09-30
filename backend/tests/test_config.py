"""Configuration tests.

The important cases here are the *environment variable* paths, not the defaults.
A previous release shipped with ``cors_origins: list[str]`` and no ``NoDecode``:
pydantic-settings json-decodes complex fields, so the comma separated value that
docker-compose passes (``http://a:1,http://b:2``) raised SettingsError and the
whole application failed to import inside the container. The default-value tests
passed, which is exactly why it shipped.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings


def test_comma_separated_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:8081,http://localhost:5173")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:8081", "http://localhost:5173"]


def test_single_origin_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://nas.local:8081")
    assert Settings(_env_file=None).cors_origins == ["http://nas.local:8081"]


def test_json_array_env_value_is_also_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["http://a:1", "http://b:2"]')
    assert Settings(_env_file=None).cors_origins == ["http://a:1", "http://b:2"]


def test_whitespace_and_empty_entries_are_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", " http://a:1 , , http://b:2 ")
    assert Settings(_env_file=None).cors_origins == ["http://a:1", "http://b:2"]


def test_defaults_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.cors_origins
    assert all(isinstance(item, str) for item in settings.cors_origins)


def test_no_complex_field_is_json_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against reintroducing a complex field without ``NoDecode``."""

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/name")
    monkeypatch.setenv("SECRET_KEY", "s3cret")
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "2.5")
    settings = Settings(_env_file=None)
    assert settings.database_url.endswith("/name")
    assert settings.secret_key == "s3cret"
    assert settings.ai_daily_budget_usd == pytest.approx(2.5)


def test_settings_are_cached() -> None:
    assert get_settings() is get_settings()


def test_app_version_comes_from_the_package() -> None:
    """Regression: `app_version` used to be hard-coded to "0.0.1" in config.py,
    so every running container reported the same version no matter which tag
    built it. It must now track the package version that version.sh writes.
    """

    import app as app_package

    settings = Settings(_env_file=None)
    assert settings.app_version == app_package.__version__.lstrip("v")
    assert not settings.app_version.startswith("v")


def test_explicit_env_version_overrides_package_default(monkeypatch) -> None:
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    assert Settings(_env_file=None).app_version == "9.9.9"
