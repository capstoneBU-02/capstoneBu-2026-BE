from sqlalchemy import BigInteger, CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, TimestampMixin


class Segment(Base, TimestampMixin):
    """원곡에서 사용자가 선택한 연습 구간."""

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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
