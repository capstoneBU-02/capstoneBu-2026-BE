from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, TimestampMixin


class Performance(Base, TimestampMixin):
    """사용자가 녹음하거나 업로드한 연주 데이터."""

    __tablename__ = "performances"
    __table_args__ = (
        CheckConstraint(
            "start_ms >= 0 AND start_ms < end_ms",
            name="range",
        ),
        CheckConstraint(
            "source IN ('RECORDING', 'UPLOAD')",
            name="source_valid",
        ),
        Index(
            "ix_performances_workspace_track_segment_created",
            "workspace_id",
            "track_id",
            "segment_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    workspace_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id"),
        nullable=False,
    )
    track_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tracks.id"),
        nullable=False,
    )
    segment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("segments.id"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    start_ms: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    end_ms: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )


class PerformanceHistory(Base):
    """신규 연주와 이전 연주의 연결 이력."""

    __tablename__ = "performance_history"
    __table_args__ = (
        UniqueConstraint(
            "performance_id",
            name="performance_once",
        ),
        Index(
            "ix_performance_history_previous_performance_id",
            "previous_performance_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    performance_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("performances.id"),
        nullable=False,
    )
    previous_performance_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("performances.id"),
        nullable=False,
    )
