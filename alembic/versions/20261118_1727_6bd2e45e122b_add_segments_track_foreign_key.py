"""segments.track_id에 tracks 외래키 추가.

Revision ID: 6bd2e45e122b
Revises: 52f7393fa422
Create Date: 2026-11-18 17:27:52.139142
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6bd2e45e122b"
down_revision: str | None = "52f7393fa422"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """segments.track_id가 tracks.id를 참조하도록 설정한다."""
    op.create_foreign_key(
        "fk_segments_track_id_tracks",
        "segments",
        "tracks",
        ["track_id"],
        ["id"],
    )


def downgrade() -> None:
    """segments의 트랙 외래키를 제거한다."""
    op.drop_constraint(
        "fk_segments_track_id_tracks",
        "segments",
        type_="foreignkey",
    )
