"""add stem part keys

Revision ID: a61b7c2d9e10
Revises: 6bd2e45e122b
Create Date: 2026-11-19 14:00:00

기타를 뺀 악기별 음원과 반주의 저장 위치 (#61). 기존 행은 null (분리 결과에 다른 악기 없음).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a61b7c2d9e10"
down_revision: str | None = "6bd2e45e122b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stems",
        sa.Column("part_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stems", "part_keys")
