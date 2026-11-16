"""create segments table

Revision ID: seg13_create
Revises: 16322865109e
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "seg13_create"
down_revision: str | None = "16322865109e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
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
            "start_ms >= 0 AND start_ms < end_ms",
            name=op.f("ck_segments_range"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_segments_version_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_segments")),
    )
    op.create_index(
        "ix_segments_track_start",
        "segments",
        ["track_id", "start_ms"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_segments_track_start", table_name="segments")
    op.drop_table("segments")
