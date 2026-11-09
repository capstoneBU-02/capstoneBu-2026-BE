"""분리·처리(JOB) 비즈니스 로직. 트랜잭션(commit)은 여기서만 한다."""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.processing import sources
from app.domains.processing.limits import hard_limit_sec
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

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# processing 상태가 단계 강제 종료 시간보다 이만큼 더 지나면 멈춘 작업으로 본다
STALE_MARGIN_SEC = 60

# 입력 자체가 잘못돼 다시 실행해도 결과가 같은 실패
NON_RETRYABLE_CODES = frozenset(
    {ErrorCode.PREPROCESS_INVALID_AUDIO.code, ErrorCode.SEPARATION_NO_GUITAR.code}
)

# pg_advisory_xact_lock의 첫 번째 키. 도메인마다 다른 값을 쓴다 (processing=1)
_LOCK_CLASS_STEMS = 1

Enqueue = Callable[[int, StemStage], None]


@dataclass(frozen=True)
class StemRequestResult:
    """준비 요청 결과. is_reused면 기존 결과를 돌려준 것."""

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
    """Celery chain 등록 (테스트에서 바꿔 끼운다)."""
    from app.domains.processing.tasks import enqueue_pipeline  # 순환 import 방지

    enqueue_pipeline(stem_id, start)


def _publish(
    db: Session,
    stem: Stem,
    start: StemStage,
    enqueue: Enqueue | None,
    *,
    undo_retry: bool = False,
) -> None:
    """commit 후 작업을 등록한다. 등록이 실패하면 503으로 알린다.

    행은 failed(AI_WORKER_ERROR)로 남겨 진행 중으로 묶이지 않게 하고, 재시도였다면 횟수를 되돌린다.
    등록 직전에 프로세스가 죽는 경우는 expire_if_stale이 queued 대기 시간으로 복구한다.
    """
    try:
        (enqueue or _default_enqueue)(stem.id, start)
    except Exception as e:
        logger.exception("stem %s 작업 등록 실패", stem.id)
        stem.status = StemStatus.FAILED
        stem.current_stage = start
        stem.error_code = ErrorCode.AI_WORKER_ERROR.code
        if undo_retry:
            stem.retry_count -= 1
        db.commit()
        raise BusinessException(ErrorCode.JOB_ENQUEUE_FAILED) from e


def _stale_deadline(stem: Stem, settings: Settings) -> datetime | None:
    """이 시각이 지나도 queued·processing이면 멈춘 작업이다."""
    if stem.status == StemStatus.QUEUED:
        return stem.updated_at + timedelta(seconds=settings.ai_job_queued_stale_sec)
    if stem.status == StemStatus.PROCESSING:
        stage = StemStage(stem.current_stage or StemStage.PREPROCESS)
        limit = hard_limit_sec(stage, settings) + STALE_MARGIN_SEC
        return stem.updated_at + timedelta(seconds=limit)
    return None


def is_stale(stem: Stem, now: datetime | None = None) -> bool:
    """등록이 유실됐거나 워커가 죽어 더 진행되지 않는 작업인지."""
    deadline = _stale_deadline(stem, get_settings())
    return deadline is not None and (now or datetime.now(UTC)) > deadline


def expire_if_stale(stem: Stem, now: datetime | None = None) -> bool:
    """멈춘 작업을 AI_WORKER_TIMEOUT 실패로 바꾼다 (재시도 가능). 잠근 행에서 호출한다."""
    if not is_stale(stem, now):
        return False
    logger.warning("stem %s 멈춘 작업 실패 처리 (status=%s)", stem.id, stem.status)
    stem.status = StemStatus.FAILED
    stem.error_code = ErrorCode.AI_WORKER_TIMEOUT.code
    return True


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
    if existing is not None and existing.status in ACTIVE_STATUSES:
        # 멈춘 작업이면 실패로 바꾸고 새 version을 만든다
        locked = db.scalars(
            select(Stem)
            .where(Stem.id == existing.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        if not expire_if_stale(locked):
            raise BusinessException(ErrorCode.STEM_IN_PROGRESS)
        db.flush()  # 부분 UNIQUE 인덱스에서 빠진 뒤 새 행을 넣는다
        existing = None
    if existing is not None:
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
    _publish(db, stem, StemStage.PREPROCESS, enqueue)
    return StemRequestResult(stem, is_reused=False)


def _select_stem(db: Session, stem_id: int, *, lock: bool) -> Stem | None:
    """삭제되지 않은 stem 조회. lock이면 행을 잠그고 최신 값으로 읽는다."""
    stmt = select(Stem).where(Stem.id == stem_id, Stem.deleted_at.is_(None))
    if lock:
        # 잠근 뒤에는 세션에 남은 예전 값이 아니라 DB의 최신 값을 써야 한다
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.scalars(stmt).first()


def get_owned_stem(db: Session, workspace_id: int, stem_id: int, *, lock: bool = False) -> Stem:
    """다른 워크스페이스 소유면 없는 것처럼 404. 멈춘 작업은 여기서 실패로 바꾼다."""
    stem = _select_stem(db, stem_id, lock=lock)
    if stem is None or stem.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.STEM_NOT_FOUND)
    if is_stale(stem):
        locked = stem if lock else _select_stem(db, stem_id, lock=True)
        if locked is not None and expire_if_stale(locked):
            db.commit()
            # 호출한 쪽이 잠금을 요청했으면 다시 잠근다 (commit으로 잠금이 풀림)
            stem = _select_stem(db, stem_id, lock=lock) or locked
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
    """단계별 마지막 시도 결과."""
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
    """error_code에 맞는 사용자 안내 문구."""
    try:
        return ErrorCode[code].message
    except KeyError:
        return ErrorCode.AI_WORKER_ERROR.message


def is_retryable(stem: Stem) -> bool:
    """재시도 버튼을 보여 줄 수 있는지."""
    return (
        stem.status == StemStatus.FAILED
        and stem.error_code not in NON_RETRYABLE_CODES
        and stem.retry_count < MAX_RETRIES
    )


def to_stem_response(db: Session, stem: Stem) -> StemResponse:
    """GET /stems/{stem_id} 응답으로 변환."""
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
    """POST /segments/{segment_id}/stems 응답으로 변환."""
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
    try:
        db.commit()
    except IntegrityError as e:  # 같은 입력으로 새로 만든 작업이 진행 중
        db.rollback()
        raise BusinessException(ErrorCode.STEM_IN_PROGRESS) from e

    _publish(db, stem, retry_from, enqueue, undo_retry=True)
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
