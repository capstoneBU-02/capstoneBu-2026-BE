from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base


class AccessType(StrEnum):
    """비공개 파일 임시 URL 접근 목적."""

    STREAM = "STREAM"
    VIEW = "VIEW"
    DOWNLOAD = "DOWNLOAD"


class FileAccessLog(Base):
    """비공개 파일(S3/SeaweedFS) 임시 URL 발급 감사 로그."""

    __tablename__ = "file_access_logs"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    file_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    access_type: Mapped[str] = mapped_column(String(20), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)