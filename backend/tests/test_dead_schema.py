"""Dead schema must not come back (ADR-093 / ADR-095).

Three tables (``features``, ``jobs``, ``job_logs``) and three columns
(``backtest_runs.parameters_id``, ``ai_models.context_length``,
``ai_models.supports_structured_output``) were declared, migrated and then never written
or read by anything. They were removed in v1.6.6.

``backend/pg.sql`` was a static ``alembic upgrade --sql`` dump of the whole schema that
no code, workflow or document referenced: a second copy of the truth, guaranteed to
drift from ``models.py`` + the migration chain the moment either changed. It is the
database-level version of the same defect v1.6.5 fixed in the scripts (ADR-090/091).

This file guards the removal the way a reviewer would: a removed column that quietly
reappears is exactly the kind of regression nobody notices, because a column that is
never written looks the same as a column that is written and happens to be NULL.
"""

from __future__ import annotations

import pathlib

from app.core.db import Base

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

REMOVED_TABLES = ("features", "jobs", "job_logs")
REMOVED_NAMES = (
    "FeatureDefinition",
    "JobLog",
    "class Job(",
    "parameters_id",
    "context_length",
    "supports_structured_output",
)


def test_the_removed_tables_are_not_in_the_metadata() -> None:
    assert set(REMOVED_TABLES) & set(Base.metadata.tables) == set()


def test_the_models_no_longer_declare_the_dead_names() -> None:
    models = (REPO_ROOT / "backend/app/domain/models.py").read_text(encoding="utf-8")

    for name in REMOVED_NAMES:
        assert name not in models, f"{name} is dead schema and must not come back"


def test_the_static_schema_dump_is_gone() -> None:
    assert not (BACKEND / "pg.sql").exists()


def test_no_second_copy_of_the_schema_lives_in_a_sql_file() -> None:
    """The truth is ``models.py`` plus the migration chain; a .sql dump is a second answer."""

    offenders = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in BACKEND.rglob("*.sql")
        if "CREATE TABLE" in path.read_text(encoding="utf-8")
    )

    assert offenders == []
