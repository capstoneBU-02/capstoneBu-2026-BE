"""link tracks

Revision ID: 0d45889dfccb
Revises: 90b30405b3f2
Create Date: 2026-11-18 00:23:28.897790

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0d45889dfccb"
down_revision: str | None = "90b30405b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _check_orphans() -> None:
    """FK를 걸기 전에 없는 트랙을 가리키는 행이 있는지 확인한다. 있으면 멈춘다."""
    conn = op.get_bind()
    for table in ("stems", "comparisons"):
        count = conn.execute(
            sa.text(
                f"SELECT count(*) FROM {table} t "
                "LEFT JOIN tracks r ON r.id = t.track_id WHERE r.id IS NULL"
            )
        ).scalar_one()
        if count:
            raise RuntimeError(
                f"{table}에 없는 트랙(track_id)을 가리키는 행이 {count}개 있습니다. "
                "정리하거나 보존 방법을 정한 뒤 다시 실행하세요."
            )


def upgrade() -> None:
    _check_orphans()
    op.create_index(op.f("ix_comparisons_track_id"), "comparisons", ["track_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_comparisons_track_id_tracks"), "comparisons", "tracks", ["track_id"], ["id"]
    )
    op.create_foreign_key(op.f("fk_stems_track_id_tracks"), "stems", "tracks", ["track_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(op.f("fk_stems_track_id_tracks"), "stems", type_="foreignkey")
    op.drop_constraint(op.f("fk_comparisons_track_id_tracks"), "comparisons", type_="foreignkey")
    op.drop_index(op.f("ix_comparisons_track_id"), table_name="comparisons")
