"""Research layer: artifacts, hypotheses and drafts (ADR-154)

Revision ID: 0013_research_layer
Revises: 0012_ai_role_contracts
Create Date: 2026-10-20 09:00:00.000000

Phase 3 gives the AI a place to put research *proposals*. Six tables, all
additive:

* ``research_artifacts`` / ``research_artifact_fragments`` — what a run read,
  stored as a hash plus short excerpts rather than as a copy of the document.
* ``ai_research_runs`` — one research request, its steps and its outcome.
* ``strategy_hypotheses`` / ``strategy_hypothesis_rules`` — what the researcher
  understood, rule by rule, with each rule's provenance.
* ``strategy_drafts`` — the formalized draft and the *server's* capability
  verdict for it.

The two links from ``ai_research_runs`` back to its hypothesis and its draft are
plain integers: a foreign key cycle would only add an ordering problem to the
migration without protecting anything the application does not already control.

Nothing here is executable. ``strategy_drafts.executable`` is part of the schema
so a draft cannot quietly become a runnable artifact; the compiler that would
produce a ``strategy_versions`` row is a later version.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_research_layer"
down_revision: str | None = "0012_ai_role_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Creation order is dependency order: PostgreSQL refuses a foreign key that
    # points at a table which does not exist yet, while SQLite accepts it happily
    # (which is why the bug this file once had reached a tag). The static guard in
    # backend/tests/test_migration_revisions.py checks every migration for it.
    op.create_table(
        "ai_research_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("current_step", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("hypothesis_id", sa.Integer(), nullable=True),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("capability_status", sa.String(length=24), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("violations_json", sa.JSON(), nullable=True),
        sa.Column("researcher_task_id", sa.Integer(), nullable=True),
        sa.Column("architect_task_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["researcher_task_id"], ["ai_tasks.id"]),
        sa.ForeignKeyConstraint(["architect_task_id"], ["ai_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_research_runs_status", "ai_research_runs", ["status", "created_at"])
    op.create_table(
        "research_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("uri", sa.String(length=1024), nullable=True),
        sa.Column("parse_status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("license_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["ai_research_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_ref", name="uq_research_artifact_ref"),
    )
    op.create_table(
        "research_artifact_fragments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("locator_json", sa.JSON(), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=False),
        sa.Column("fragment_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["research_artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_fragment_artifact", "research_artifact_fragments", ["artifact_id"]
    )
    op.create_table(
        "strategy_hypotheses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("strategy_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
        sa.Column("understanding", sa.Text(), nullable=True),
        sa.Column("confidence_self_reported", sa.String(length=16), nullable=True),
        sa.Column("role", sa.String(length=48), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("provider_name", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("ai_task_id", sa.Integer(), nullable=True),
        sa.Column("hypothesis_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["ai_research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ai_task_id"], ["ai_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_hypotheses_run", "strategy_hypotheses", ["run_id"])
    op.create_table(
        "strategy_hypothesis_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(length=32), nullable=False),
        sa.Column("field", sa.String(length=24), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column("capability_status", sa.String(length=24), nullable=True),
        sa.Column("required_capabilities_json", sa.JSON(), nullable=True),
        sa.Column("evidence_fragment_ids_json", sa.JSON(), nullable=True),
        sa.Column("parameters_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["strategy_hypotheses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id", "rule_key", name="uq_strategy_hypothesis_rule"),
    )
    op.create_table(
        "strategy_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("capability_status", sa.String(length=24), nullable=False),
        sa.Column("model_status", sa.String(length=24), nullable=True),
        sa.Column("executable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_experimental_of", sa.Integer(), nullable=True),
        sa.Column("compiled_strategy_version_id", sa.Integer(), nullable=True),
        sa.Column("ai_task_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("draft_json", sa.JSON(), nullable=True),
        sa.Column("capability_report_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["ai_research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["strategy_hypotheses.id"]),
        sa.ForeignKeyConstraint(["compiled_strategy_version_id"], ["strategy_versions.id"]),
        sa.ForeignKeyConstraint(["ai_task_id"], ["ai_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_drafts_run", "strategy_drafts", ["run_id"])


def downgrade() -> None:
    # Reverse of the order above: a table that something points at is dropped last.
    op.drop_index("ix_strategy_drafts_run", table_name="strategy_drafts")
    op.drop_table("strategy_drafts")
    op.drop_table("strategy_hypothesis_rules")
    op.drop_index("ix_strategy_hypotheses_run", table_name="strategy_hypotheses")
    op.drop_table("strategy_hypotheses")
    op.drop_index("ix_research_fragment_artifact", table_name="research_artifact_fragments")
    op.drop_table("research_artifact_fragments")
    op.drop_table("research_artifacts")
    op.drop_index("ix_ai_research_runs_status", table_name="ai_research_runs")
    op.drop_table("ai_research_runs")
