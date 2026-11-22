"""refine data tables and merge heads

Revision ID: c8a1f3e92014
Revises: b6160a57056e, 9c0b8c721792
Create Date: 2026-11-22 20:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8a1f3e92014"
down_revision: str | tuple[str, str] | None = ("b6160a57056e", "9c0b8c721792")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """사용하지 않는 tab_results, media_tasks 테이블을 제거한다."""
    op.drop_index(op.f("ix_tab_results_workspace_id"), table_name="tab_results")
    op.drop_index(op.f("ix_tab_results_task_id"), table_name="tab_results")
    op.drop_index(op.f("ix_tab_results_segment_id"), table_name="tab_results")
    op.drop_table("tab_results")

    op.drop_index(op.f("ix_media_tasks_workspace_id"), table_name="media_tasks")
    op.drop_index(op.f("ix_media_tasks_track_id"), table_name="media_tasks")
    op.drop_index(op.f("ix_media_tasks_segment_id"), table_name="media_tasks")
    op.drop_table("media_tasks")


def downgrade() -> None:
    """media_tasks, tab_results 테이블을 복원한다."""
    op.create_table(
        "media_tasks",
        sa.Column("task_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=False),
        sa.Column("segment_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_task_id", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("is_reused", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["parent_task_id"],
            ["media_tasks.task_id"],
            name=op.f("fk_media_tasks_parent_task_id_media_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["segments.id"],
            name=op.f("fk_media_tasks_segment_id_segments"),
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_media_tasks_track_id_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_media_tasks_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_media_tasks")),
    )
    op.create_index(op.f("ix_media_tasks_segment_id"), "media_tasks", ["segment_id"], unique=False)
    op.create_index(op.f("ix_media_tasks_track_id"), "media_tasks", ["track_id"], unique=False)
    op.create_index(
        op.f("ix_media_tasks_workspace_id"), "media_tasks", ["workspace_id"], unique=False
    )

    op.create_table(
        "tab_results",
        sa.Column("tab_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("segment_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("file_key", sa.String(length=512), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["segments.id"],
            name=op.f("fk_tab_results_segment_id_segments"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["media_tasks.task_id"],
            name=op.f("fk_tab_results_task_id_media_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_tab_results_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("tab_id", name=op.f("pk_tab_results")),
    )
    op.create_index(op.f("ix_tab_results_segment_id"), "tab_results", ["segment_id"], unique=False)
    op.create_index(op.f("ix_tab_results_task_id"), "tab_results", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_tab_results_workspace_id"), "tab_results", ["workspace_id"], unique=False
    )
