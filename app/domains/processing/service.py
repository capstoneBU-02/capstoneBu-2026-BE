"""분리·처리(JOB) 비즈니스 로직. 트랜잭션(commit)은 여기서만 한다."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.processing import sources
from app.domains.processing.models import (
    ACTIVE_STATUSES,
    READY_STATUSES,
    STAGE_ORDER,
    StageStatus,
    Stem,
    StemStage,
    StemStageResult,
    StemStatus,
)
from app.domains.processing.schemas import (
    CancelResponse,
    FailureView,
    RetryResponse,
    StageView,
    StemCreatedResponse,
    StemResponse,
)

MAX_RETRIES = 3

# 입력 자체가 잘못돼 다시 실행해도 결과가 같은 실패
NON_RETRYABLE_CODES = frozenset(
    {ErrorCode.PREPROCESS_INVALID_AUDIO.code, ErrorCode.SEPARATION_NO_GUITAR.code}
)

# pg_advisory_xact_lock의 첫 번째 키. 도메인마다 다른 값을 쓴다 (processing=1)
_LOCK_CLASS_STEMS = 1

Enqueue = Callable[[int, StemStage], None]


@dataclass(frozen=True)
class StemRequestResult:
    stem: Stem
    is_reused: bool


def compute_input_hash(src: sources.SegmentSource, settings: Settings) -> str:
    """같은 값이면 같은 결과가 나오는 입력 묶음. 하나라도 바뀌면 새 version을 만든다."""
    payload = {
        "workspace_id": src.workspace_id,
        "segment_id": src.segment_id,
        "file_hash": src.track_file_hash,
        "start_ms": src.start_ms,
        "end_ms": src.end_ms,
        "bypass_separation": src.is_separated_stem,
        "separation_model": None if src.is_separated_stem else settings.ai_separation_model,
        "config_version": settings.ai_config_version,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _lock_segment(db: Session, segment_id: int) -> None:
    """같은 구간 요청을 한 줄로 세운다. 트랜잭션이 끝나면 자동으로 풀린다."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :obj)"),
        {"cls": _LOCK_CLASS_STEMS, "obj": segment_id},
    )


def _default_enqueue(stem_id: int, start: StemStage) -> None:
    from app.domains.processing.tasks import enqueue_pipeline  # 순환 import 방지

    enqueue_pipeline(stem_id, start)


def request_stem(
    db: Session,
    workspace_id: int,
    segment_id: int,
    *,
    enqueue: Enqueue | None = None,
) -> StemRequestResult:
    """기타 음원 준비 요청. 같은 입력이 진행 중이면 409, 완료돼 있으면 재사용한다."""
    settings = get_settings()
    src = sources.load_segment(db, workspace_id, segment_id)
    _lock_segment(db, segment_id)
    input_hash = compute_input_hash(src, settings)

    existing = db.scalars(
        select(Stem)
        .where(
            Stem.input_hash == input_hash,
            Stem.deleted_at.is_(None),
            Stem.status.in_([*ACTIVE_STATUSES, *READY_STATUSES]),
        )
        .order_by(Stem.version.desc())
        .limit(1)
    ).first()
    if existing is not None:
        if existing.status in ACTIVE_STATUSES:
            raise BusinessException(ErrorCode.STEM_IN_PROGRESS)
        db.rollback()  # 잠금 해제
        return StemRequestResult(existing, is_reused=True)

    last_version = db.scalar(
        select(func.coalesce(func.max(Stem.version), 0)).where(Stem.segment_id == segment_id)
    )
    stem = Stem(
        workspace_id=src.workspace_id,
        track_id=src.track_id,
        segment_id=src.segment_id,
        start_ms=src.start_ms,
        end_ms=src.end_ms,
        version=int(last_version or 0) + 1,
        part="guitar",
        status=StemStatus.QUEUED,
        bypass_separation=src.is_separated_stem,
        input_hash=input_hash,
        config_version=settings.ai_config_version,
        retry_count=0,
    )
    db.add(stem)
    try:
        db.commit()
    except IntegrityError as e:  # 잠금을 거치지 않은 동시 요청의 마지막 방어선
        db.rollback()
        raise BusinessException(ErrorCode.STEM_IN_PROGRESS) from e

    # commit 후에 등록해야 worker가 아직 없는 행을 읽지 않는다
    (enqueue or _default_enqueue)(stem.id, StemStage.PREPROCESS)
    return StemRequestResult(stem, is_reused=False)


def get_owned_stem(db: Session, workspace_id: int, stem_id: int, *, lock: bool = False) -> Stem:
    """다른 워크스페이스 소유면 없는 것처럼 404."""
    stmt = select(Stem).where(Stem.id == stem_id, Stem.deleted_at.is_(None))
    if lock:
        # 잠근 뒤에는 세션에 남은 예전 값이 아니라 DB의 최신 값을 써야 한다
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    stem = db.scalars(stmt).first()
    if stem is None or stem.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.STEM_NOT_FOUND)
    return stem


def get_current_stem(db: Session, segment_id: int, start_ms: int, end_ms: int) -> Stem | None:
    """구간의 현재 기타 음원: 현재 범위와 같은 범위로 만든 것 중 version이 가장 큰 것.

    TAB·CMP도 이 함수로 입력 기타 음원을 찾는다. 상태는 호출한 쪽에서 확인한다.
    """
    return db.scalars(
        select(Stem)
        .where(
            Stem.segment_id == segment_id,
            Stem.start_ms == start_ms,
            Stem.end_ms == end_ms,
            Stem.deleted_at.is_(None),
        )
        .order_by(Stem.version.desc())
        .limit(1)
    ).first()


def _latest_stage_results(db: Session, stem_id: int) -> dict[str, StemStageResult]:
    rows = db.scalars(
        select(StemStageResult)
        .where(StemStageResult.stem_id == stem_id)
        .order_by(StemStageResult.stage, StemStageResult.attempt)
    ).all()
    latest: dict[str, StemStageResult] = {}
    for row in rows:
        latest[row.stage] = row  # attempt 오름차순이므로 마지막이 최신
    return latest


def _error_message(code: str) -> str:
    try:
        return ErrorCode[code].message
    except KeyError:
        return ErrorCode.AI_WORKER_ERROR.message


def is_retryable(stem: Stem) -> bool:
    return (
        stem.status == StemStatus.FAILED
        and stem.error_code not in NON_RETRYABLE_CODES
        and stem.retry_count < MAX_RETRIES
    )


def to_stem_response(db: Session, stem: Stem) -> StemResponse:
    latest = _latest_stage_results(db, stem.id)
    stages = [
        StageView(
            stage=stage,
            status=latest[stage].status if stage in latest else StageStatus.QUEUED,
        )
        for stage in STAGE_ORDER
    ]
    failure = None
    if stem.status == StemStatus.FAILED and stem.error_code:
        failure = FailureView(
            stage=stem.current_stage,
            error_code=stem.error_code,
            message=_error_message(stem.error_code),
            retryable=is_retryable(stem),
            retry_count=stem.retry_count,
            max_retries=MAX_RETRIES,
            retries_left=max(MAX_RETRIES - stem.retry_count, 0),
        )
    return StemResponse(
        stem_id=stem.id,
        segment_id=stem.segment_id,
        version=stem.version,
        start_ms=stem.start_ms,
        end_ms=stem.end_ms,
        status=stem.status,
        current_stage=stem.current_stage,
        bypass_separation=stem.bypass_separation,
        playable=stem.status in READY_STATUSES and stem.storage_key is not None,
        review_reason=stem.review_reason,
        stages=stages,
        failure=failure,
        updated_at=stem.updated_at,
    )


def to_created_response(result: StemRequestResult) -> StemCreatedResponse:
    s = result.stem
    return StemCreatedResponse(
        stem_id=s.id,
        segment_id=s.segment_id,
        version=s.version,
        start_ms=s.start_ms,
        end_ms=s.end_ms,
        status=s.status,
        bypass_separation=s.bypass_separation,
        is_reused=result.is_reused,
        created_at=s.created_at,
    )


def retry_stem(
    db: Session, workspace_id: int, stem_id: int, *, enqueue: Enqueue | None = None
) -> RetryResponse:
    """실패한 단계부터 다시 실행한다. 같은 stem_id로 실행하므로 version은 그대로."""
    stem = get_owned_stem(db, workspace_id, stem_id, lock=True)
    if stem.status != StemStatus.FAILED or stem.error_code in NON_RETRYABLE_CODES:
        raise BusinessException(ErrorCode.STEM_NOT_RETRYABLE)
    if stem.retry_count >= MAX_RETRIES:
        raise BusinessException(ErrorCode.RETRY_LIMIT_EXCEEDED)

    retry_from = StemStage(stem.current_stage or StemStage.PREPROCESS)
    stem.retry_count += 1
    stem.status = StemStatus.QUEUED
    stem.error_code = None
    db.commit()

    (enqueue or _default_enqueue)(stem.id, retry_from)
    return RetryResponse(
        stem_id=stem.id,
        status=stem.status,
        retry_from=retry_from,
        retry_count=stem.retry_count,
        max_retries=MAX_RETRIES,
        retries_left=MAX_RETRIES - stem.retry_count,
    )


def cancel_stem(db: Session, workspace_id: int, stem_id: int) -> CancelResponse:
    """대기 중(queued)인 작업만 중단한다. 실행 중 중단은 D12 확정 후."""
    stem = get_owned_stem(db, workspace_id, stem_id, lock=True)
    if stem.status != StemStatus.QUEUED:
        raise BusinessException(ErrorCode.STEM_NOT_CANCELABLE)
    stem.status = StemStatus.CANCELED
    db.commit()
    return CancelResponse(stem_id=stem.id, status=stem.status)
