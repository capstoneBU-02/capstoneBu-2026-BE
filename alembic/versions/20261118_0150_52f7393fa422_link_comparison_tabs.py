"""link comparison tabs

Revision ID: 52f7393fa422
Revises: c3936d94302a
Create Date: 2026-11-18 01:50:06.062859

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "52f7393fa422"
down_revision: str | None = "c3936d94302a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _check_orphans() -> None:
    """FK를 걸기 전에 없는 타브를 가리키는 비교가 있는지 확인한다. 있으면 멈춘다."""
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM comparisons c LEFT JOIN tabs t ON t.id = c.tab_id "
                "WHERE c.tab_id IS NOT NULL AND t.id IS NULL"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            f"comparisons에 없는 타브(tab_id)를 가리키는 행이 {count}개 있습니다. "
            "정리하거나 보존 방법을 정한 뒤 다시 실행하세요."
        )


def upgrade() -> None:
    _check_orphans()
    op.create_index(op.f("ix_comparisons_tab_id"), "comparisons", ["tab_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_comparisons_tab_id_tabs"),
        "comparisons",
        "tabs",
        ["tab_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_comparisons_tab_id_tabs"), "comparisons", type_="foreignkey")
    op.drop_index(op.f("ix_comparisons_tab_id"), table_name="comparisons")
