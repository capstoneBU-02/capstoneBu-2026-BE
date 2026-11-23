"""add performances

Revision ID: 9c0b8c721792
Revises: bb18acb820bd
Create Date: 2026-11-20 13:40:01.940611

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c0b8c721792"
down_revision: str | None = "bb18acb820bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """연주 및 연주 변경 이력 테이블을 생성한다."""
    op.create_table(
        "performances",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=False),
        sa.Column("segment_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
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
        sa.CheckConstraint(
            "source IN ('RECORDING', 'UPLOAD')",
            name=op.f("ck_performances_source_valid"),
        ),
        sa.CheckConstraint(
            "start_ms >= 0 AND start_ms < end_ms",
            name=op.f("ck_performances_range"),
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["segments.id"],
            name=op.f("fk_performances_segment_id_segments"),
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_performances_track_id_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_performances_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performances")),
    )

    op.create_index(
        "ix_performances_workspace_track_segment_created",
        "performances",
        ["workspace_id", "track_id", "segment_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "performance_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("performance_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_performance_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["performance_id"],
            ["performances.id"],
            name=op.f("fk_performance_history_performance_id_performances"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_performance_id"],
            ["performances.id"],
            name=op.f("fk_performance_history_previous_performance_id_performances"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performance_history")),
        sa.UniqueConstraint("performance_id", name="performance_once"),
    )

    op.create_index(
        "ix_performance_history_previous_performance_id",
        "performance_history",
        ["previous_performance_id"],
        unique=False,
    )


def downgrade() -> None:
    """연주 및 연주 변경 이력 테이블을 제거한다."""
    op.drop_index(
        "ix_performance_history_previous_performance_id",
        table_name="performance_history",
    )
    op.drop_table("performance_history")

    op.drop_index(
        "ix_performances_workspace_track_segment_created",
        table_name="performances",
    )
    op.drop_table("performances")
