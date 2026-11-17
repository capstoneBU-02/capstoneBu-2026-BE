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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, SoftDeleteMixin, TimestampMixin


class TabStatus(StrEnum):
    """타브 생성 작업 상태. needs_review는 실패가 아니라 확인이 필요한 결과다."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class TabStage(StrEnum):
    """처리 단계. transcription → rhythm → tab 순서로 실행한다."""

    TRANSCRIPTION = "transcription"
    RHYTHM = "rhythm"
    TAB = "tab"


class StageStatus(StrEnum):
    """단계 결과 상태 (tab_stage_results.status)."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


class ReviewReason(StrEnum):
    """needs_review 이유 (tabs.review_reasons)."""

    STEM_NEEDS_REVIEW = "STEM_NEEDS_REVIEW"
    LOW_CONFIDENCE_NOTES = "LOW_CONFIDENCE_NOTES"
    UNPLAYABLE_POSITIONS = "UNPLAYABLE_POSITIONS"
    TEMPO_UNCERTAIN = "TEMPO_UNCERTAIN"
    NON_4_4_METER = "NON_4_4_METER"


ACTIVE_STATUSES = (TabStatus.QUEUED, TabStatus.PROCESSING)
DONE_STATUSES = (TabStatus.SUCCESS, TabStatus.NEEDS_REVIEW)
STAGE_ORDER = (TabStage.TRANSCRIPTION, TabStage.RHYTHM, TabStage.TAB)


class Tab(Base, TimestampMixin, SoftDeleteMixin):
    """구간의 타브 생성 작업과 결과. 다시 만들면 새 행(version + 1)을 만들고 이전 결과는 남긴다.

    workspace_id는 workspaces FK, segment_id는 segments FK.
    track_id는 IN 모델이 들어오면 FK로 바꾼다.
    """

    __tablename__ = "tabs"
    __table_args__ = (
        UniqueConstraint("segment_id", "version"),
        CheckConstraint("capo >= 0", name="capo"),
        # 같은 입력의 진행 중 작업은 하나만 (TAB_IN_PROGRESS)
        Index(
            "uq_tabs_active_input_hash",
            "input_hash",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        Index("ix_tabs_input_hash_status", "input_hash", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id"), index=True)
    track_id: Mapped[int] = mapped_column(BigInteger)  # 저장소 key용
    # segment_id 단독 인덱스는 두지 않는다 (UNIQUE(segment_id, version)가 대신)
    segment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("segments.id"))
    # 이 타브를 만든 기타 음원. 현재 기타 음원과 다르면 "이전 구간 기준"
    stem_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stems.id"), index=True)
    version: Mapped[int]
    result_type: Mapped[str] = mapped_column(String(20), default="original")
    tuning_id: Mapped[str] = mapped_column(String(30), default="e_standard")
    capo: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default=TabStatus.QUEUED)
    current_stage: Mapped[str | None] = mapped_column(String(20))
    review_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    input_hash: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(20))
    retry_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(50))


class TabStageResult(Base):
    """단계별 처리 결과. 재시도마다 attempt를 올려 새 행을 만든다."""

    __tablename__ = "tab_stage_results"
    __table_args__ = (UniqueConstraint("tab_id", "stage", "attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # tab_id 단독 인덱스는 두지 않는다: (tab_id, stage, attempt) UNIQUE가 대신한다
    tab_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tabs.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(50))
    output_key: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
