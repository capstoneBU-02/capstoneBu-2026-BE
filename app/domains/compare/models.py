from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, SoftDeleteMixin, TimestampMixin


class ComparisonStatus(StrEnum):
    """비교 작업 상태. not_comparable은 실패가 아니라 처리가 끝난 상태다."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    NOT_COMPARABLE = "not_comparable"
    FAILED = "failed"


class ComparisonStage(StrEnum):
    """처리 단계. 판정 함수(복재환)와 같은 이름을 쓴다."""

    ALIGN = "align"
    TRANSCRIPTION = "transcription"
    COMPARE = "compare"


class StageStatus(StrEnum):
    """단계 결과 상태 (comparison_stage_results.status)."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # 앞 단계에서 not_comparable로 끝나 실행하지 않음


class RegionType(StrEnum):
    """다시 들어 볼 구간 종류. unavailable은 판단 보류 (CMP-05)."""

    PITCH_DIFFERENCE = "pitch_difference"
    MISSING_NOTE = "missing_note"
    TIMING_DIFFERENCE = "timing_difference"
    UNAVAILABLE = "unavailable"


ACTIVE_STATUSES = (ComparisonStatus.QUEUED, ComparisonStatus.PROCESSING)
DONE_STATUSES = (ComparisonStatus.SUCCESS, ComparisonStatus.NOT_COMPARABLE)
STAGE_ORDER = (ComparisonStage.ALIGN, ComparisonStage.TRANSCRIPTION, ComparisonStage.COMPARE)


class Comparison(Base, TimestampMixin, SoftDeleteMixin):
    """연주 비교 작업과 결과. 결과 본문은 result_key(JSON), 구간은 comparison_regions.

    workspace_id·performance_id·segment_id·tab_id는 ACC·REC·SEG·TAB 모델이 들어오면 FK로 바꾼다.
    """

    __tablename__ = "comparisons"
    __table_args__ = (
        # 같은 입력의 진행 중 작업은 하나만 (COMPARISON_IN_PROGRESS)
        Index(
            "uq_comparisons_active_input_hash",
            "input_hash",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        Index("ix_comparisons_input_hash_status", "input_hash", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int] = mapped_column(BigInteger)  # 저장소 key용
    performance_id: Mapped[int] = mapped_column(BigInteger, index=True)
    segment_id: Mapped[int] = mapped_column(BigInteger, index=True)
    stem_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stems.id"), index=True)
    tab_id: Mapped[int | None] = mapped_column(BigInteger)  # 기준 타브 (없으면 null)
    start_ms: Mapped[int]  # 비교한 구간 범위 (연주 당시 = 기준 음원 범위)
    end_ms: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default=ComparisonStatus.QUEUED)
    current_stage: Mapped[str | None] = mapped_column(String(20))
    not_comparable_reason: Mapped[str | None] = mapped_column(String(50))
    input_hash: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(20))
    retry_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(50))
    result_key: Mapped[str | None] = mapped_column(String(500))


class ComparisonStageResult(Base):
    """단계별 처리 결과. 재시도마다 attempt를 올려 새 행을 만든다."""

    __tablename__ = "comparison_stage_results"
    __table_args__ = (UniqueConstraint("comparison_id", "stage", "attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # comparison_id 단독 인덱스는 두지 않는다 (UNIQUE가 대신)
    comparison_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("comparisons.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(50))
    output_key: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ComparisonRegion(Base):
    """다시 들어 볼 구간과 사용자의 확인 여부. 시간은 구간 시작 기준 밀리초."""

    __tablename__ = "comparison_regions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("comparisons.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(30))
    start_ms: Mapped[int]  # 기준 음원 기준
    end_ms: Mapped[int]
    performance_start_ms: Mapped[int]  # 같은 위치의 내 연주 기준
    performance_end_ms: Mapped[int]
    reason: Mapped[str | None] = mapped_column(String(50))  # unavailable일 때만
    is_checked: Mapped[bool] = mapped_column(default=False)
