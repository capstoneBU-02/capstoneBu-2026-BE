"""link workspaces

Revision ID: 7e89085d6c4c
Revises: 888f59a294f5
Create Date: 2026-11-17 02:12:48.449645

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7e89085d6c4c"
down_revision: str | None = "888f59a294f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _check_orphans() -> None:
    """FK를 걸기 전에 없는 워크스페이스를 가리키는 행이 있는지 확인한다. 있으면 멈춘다."""
    conn = op.get_bind()
    for table in ("stems", "comparisons"):
        count = conn.execute(
            sa.text(
                f"SELECT count(*) FROM {table} t "
                "LEFT JOIN workspaces w ON w.id = t.workspace_id WHERE w.id IS NULL"
            )
        ).scalar_one()
        if count:
            raise RuntimeError(
                f"{table}에 없는 워크스페이스(workspace_id)를 가리키는 행이 {count}개 있습니다. "
                "정리하거나 보존 방법을 정한 뒤 다시 실행하세요."
            )


def upgrade() -> None:
    _check_orphans()
    op.create_foreign_key(
        op.f("fk_comparisons_workspace_id_workspaces"),
        "comparisons",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_stems_workspace_id_workspaces"), "stems", "workspaces", ["workspace_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_stems_workspace_id_workspaces"), "stems", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_comparisons_workspace_id_workspaces"), "comparisons", type_="foreignkey"
    )
