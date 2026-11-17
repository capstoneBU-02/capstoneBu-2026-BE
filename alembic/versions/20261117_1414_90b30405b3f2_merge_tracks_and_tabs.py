"""merge tracks and tabs

add_tracks(#28)와 add_tabs(#30)가 같은 리비전(link_workspaces) 뒤에 따로 머지되어
head가 두 개가 됐다. 스키마 변경 없이 두 갈래를 하나로 합친다.

Revision ID: 90b30405b3f2
Revises: a28e73f43072, 5b7e2c1d9a4f
Create Date: 2026-11-17 14:14:40.621365

"""

from collections.abc import Sequence

revision: str = "90b30405b3f2"
down_revision: str | Sequence[str] | None = ("a28e73f43072", "5b7e2c1d9a4f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
