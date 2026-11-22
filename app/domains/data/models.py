from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base


class AccessType(StrEnum):
    """비공개 파일 임시 URL 접근 목적."""

    STREAM = "STREAM"
    VIEW = "VIEW"
    DOWNLOAD = "DOWNLOAD"


class MediaTask(Base):
    """레거시 작업 이력 모델 (conftest 호환용, 신규 마이그레이션에서 테이블 제거됨)."""

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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    is_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TabResult(Base):
    """레거시 타브 결과물 모델 (conftest 호환용, 신규 마이그레이션에서 테이블 제거됨)."""

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
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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