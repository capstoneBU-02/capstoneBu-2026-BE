from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, TimestampMixin


class AccessType(StrEnum):
    """비공개 파일 접근 유형 (DATA-05)."""

    STREAM = "STREAM"
    VIEW = "VIEW"
    DOWNLOAD = "DOWNLOAD"


class MediaTask(Base, TimestampMixin):
    """작업 이력 및 버전 관리 (DATA-02)."""

    __tablename__ = "media_tasks"

    task_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    track_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tracks.id"), nullable=False, index=True
    )
    segment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("segments.id"), nullable=False, index=True
    )
    parent_task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("media_tasks.task_id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    is_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TabResult(Base):
    """타브 결과물 파일 참조 (DATA-01)."""

    __tablename__ = "tab_results"

    tab_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("media_tasks.task_id"), nullable=True, index=True
    )
    segment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("segments.id"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="musicxml")
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FileAccessLog(Base):
    """보안 자원 접근 및 링크 발급 기록 (DATA-05)."""

    __tablename__ = "file_access_logs"

    access_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    access_type: Mapped[str] = mapped_column(String(20), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)