from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, TimestampMixin


class Segment(Base, TimestampMixin):
    """원본 음원에서 사용자가 선택한 연습 구간."""

    __tablename__ = "segments"
    __table_args__ = (
        CheckConstraint(
            "start_ms >= 0 AND start_ms < end_ms",
            name="range",
        ),
        CheckConstraint(
            "version >= 1",
            name="version_positive",
        ),
        Index("ix_segments_track_start", "track_id", "start_ms"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    track_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tracks.id"),
        nullable=False,
    )
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)


class SegmentHistory(Base):
    """구간 수정 전의 정보를 보관하는 변경 이력."""

    __tablename__ = "segment_history"
    __table_args__ = (
        CheckConstraint(
            "start_ms >= 0 AND start_ms < end_ms",
            name="segment_history_range",
        ),
        CheckConstraint(
            "version >= 1",
            name="segment_history_version_positive",
        ),
        Index(
            "ix_segment_history_segment_id",
            "segment_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    segment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("segments.id"),
        nullable=False,
    )
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
