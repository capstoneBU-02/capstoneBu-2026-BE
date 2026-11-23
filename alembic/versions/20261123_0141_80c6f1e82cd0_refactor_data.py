"""rename access_id to log_id and add file_key index

Revision ID: 80c6f1e82cd0
Revises: c8a1f3e92014, 9c0b8c721792
Create Date: ...
"""

from alembic import op

revision: str = "80c6f1e82cd0"
down_revision: str | tuple[str, ...] | None = ("c8a1f3e92014", "9c0b8c721792")


def upgrade() -> None:
    """file_access_logs의 access_id 컬럼명을 log_id로 변경하고 file_key 인덱스를 추가한다."""
    op.alter_column("file_access_logs", "access_id", new_column_name="log_id")
    op.create_index(
        op.f("ix_file_access_logs_file_key"),
        "file_access_logs",
        ["file_key"],
        unique=False,
    )


def downgrade() -> None:
    """file_key 인덱스를 제거하고 log_id 컬럼명을 access_id로 복원한다."""
    op.drop_index(op.f("ix_file_access_logs_file_key"), table_name="file_access_logs")
    op.alter_column("file_access_logs", "log_id", new_column_name="access_id")
