from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, SoftDeleteMixin, TimestampMixin


class StemStatus(StrEnum):
    """기타 음원 작업 상태. success·needs_review면 타브 생성 가능."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELED = "canceled"


class StemStage(StrEnum):
    """처리 단계. 분리 음원은 separation을 skipped로 남긴다."""

    PREPROCESS = "preprocess"
    SEPARATION = "separation"


class StageStatus(StrEnum):
    """단계 결과 상태 (stem_stage_results.status)."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


ACTIVE_STATUSES = (StemStatus.QUEUED, StemStatus.PROCESSING)
READY_STATUSES = (StemStatus.SUCCESS, StemStatus.NEEDS_REVIEW)
STAGE_ORDER = (StemStage.PREPROCESS, StemStage.SEPARATION)


class Stem(Base, TimestampMixin, SoftDeleteMixin):
    """구간의 기타 음원 준비 작업과 결과. 다시 만들면 새 행(version + 1)을 만든다.

    workspace_id·track_id·segment_id는 ACC·IN·SEG 모델이 들어오면 FK로 바꾼다.
    """

    __tablename__ = "stems"
    __table_args__ = (
        UniqueConstraint("segment_id", "version"),
        CheckConstraint("start_ms >= 0 AND start_ms < end_ms", name="range"),
        # 같은 입력의 진행 중 작업은 하나만 (STEM_IN_PROGRESS)
        Index(
            "uq_stems_active_input_hash",
            "input_hash",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        # 현재 기타 음원 조회: 현재 구간 범위와 같은 것 중 version이 가장 큰 것
        Index("ix_stems_current", "segment_id", "start_ms", "end_ms", "version"),
        Index("ix_stems_input_hash_status", "input_hash", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # segment_id 단독 인덱스는 두지 않는다 (UNIQUE(segment_id, version)·ix_stems_current가 대신)
    segment_id: Mapped[int] = mapped_column(BigInteger)
    start_ms: Mapped[int]  # 작업을 만들 때의 구간 범위. 구간을 수정해도 바뀌지 않는다
    end_ms: Mapped[int]
    version: Mapped[int]
    part: Mapped[str] = mapped_column(String(20), default="guitar")
    status: Mapped[str] = mapped_column(String(20), default=StemStatus.QUEUED)
    current_stage: Mapped[str | None] = mapped_column(String(20))
    bypass_separation: Mapped[bool]
    storage_key: Mapped[str | None] = mapped_column(String(500))  # guitar.wav
    # preprocess가 실제로 넣은 앞쪽 여유. separate는 설정값이 아니라 이 값으로 자른다
    input_pad_ms: Mapped[int | None]
    review_reason: Mapped[str | None] = mapped_column(String(50))
    input_hash: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(20))
    retry_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(50))


class StemStageResult(Base):
    """단계별 처리 결과. 재시도마다 attempt를 올려 새 행을 만든다."""

    __tablename__ = "stem_stage_results"
    __table_args__ = (UniqueConstraint("stem_id", "stage", "attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # stem_id 단독 인덱스는 두지 않는다: (stem_id, stage, attempt) UNIQUE가 대신한다
    stem_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stems.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(50))
    output_key: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
