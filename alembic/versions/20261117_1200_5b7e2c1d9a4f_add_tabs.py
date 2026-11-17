"""add tabs

Revision ID: 5b7e2c1d9a4f
Revises: 7e89085d6c4c
Create Date: 2026-11-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "5b7e2c1d9a4f"
down_revision: str | None = "7e89085d6c4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tabs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=False),
        sa.Column("segment_id", sa.BigInteger(), nullable=False),
        sa.Column("stem_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("result_type", sa.String(length=20), nullable=False),
        sa.Column("tuning_id", sa.String(length=30), nullable=False),
        sa.Column("capo", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_stage", sa.String(length=20), nullable=True),
        sa.Column("review_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("config_version", sa.String(length=20), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("capo >= 0", name=op.f("ck_tabs_capo")),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name=op.f("fk_tabs_segment_id_segments")
        ),
        sa.ForeignKeyConstraint(["stem_id"], ["stems.id"], name=op.f("fk_tabs_stem_id_stems")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_tabs_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tabs")),
        sa.UniqueConstraint("segment_id", "version", name=op.f("uq_tabs_segment_id")),
    )
    op.create_index("ix_tabs_input_hash_status", "tabs", ["input_hash", "status"], unique=False)
    op.create_index(op.f("ix_tabs_stem_id"), "tabs", ["stem_id"], unique=False)
    op.create_index(op.f("ix_tabs_workspace_id"), "tabs", ["workspace_id"], unique=False)
    op.create_index(
        "uq_tabs_active_input_hash",
        "tabs",
        ["input_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    op.create_table(
        "tab_stage_results",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tab_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("output_key", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tab_id"],
            ["tabs.id"],
            name=op.f("fk_tab_stage_results_tab_id_tabs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tab_stage_results")),
        sa.UniqueConstraint("tab_id", "stage", "attempt", name=op.f("uq_tab_stage_results_tab_id")),
    )


def downgrade() -> None:
    op.drop_table("tab_stage_results")
    op.drop_index(
        "uq_tabs_active_input_hash",
        table_name="tabs",
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    op.drop_index(op.f("ix_tabs_workspace_id"), table_name="tabs")
    op.drop_index(op.f("ix_tabs_stem_id"), table_name="tabs")
    op.drop_index("ix_tabs_input_hash_status", table_name="tabs")
    op.drop_table("tabs")
