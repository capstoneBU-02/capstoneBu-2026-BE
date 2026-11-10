"""연주 비교(CMP) 비즈니스 로직. 트랜잭션(commit)은 여기서만 한다."""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.compare import sources
from app.domains.compare.limits import hard_limit_sec
from app.domains.compare.models import (
    ACTIVE_STATUSES,
    DONE_STATUSES,
    STAGE_ORDER,
    Comparison,
    ComparisonRegion,
    ComparisonStage,
    ComparisonStageResult,
    ComparisonStatus,
    RegionType,
    StageStatus,
)
from app.domains.compare.schemas import (
    BaselineView,
    ComparisonCreatedResponse,
    ComparisonResponse,
    ComparisonResultResponse,
    FailureView,
    OverallTempoView,
    PlaybackResponse,
    PlaybackTrackView,
    RegionCheckResponse,
    RegionView,
    RetryResponse,
    SegmentView,
    StageView,
    SummaryView,
)
from app.domains.processing import service as stem_service
from app.domains.processing import sources as stem_sources
from app.domains.processing.models import READY_STATUSES, Stem

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
STALE_MARGIN_SEC = 60

# 입력 자체가 잘못돼 다시 실행해도 결과가 같은 실패
NON_RETRYABLE_CODES = frozenset(
    {ErrorCode.PERFORMANCE_INVALID_AUDIO.code, ErrorCode.STEM_MISSING.code}
)

# pg_advisory_xact_lock의 첫 번째 키 (processing=1, compare=2)
_LOCK_CLASS_COMPARISONS = 2

Enqueue = Callable[[int, ComparisonStage], None]


@dataclass(frozen=True)
class ComparisonRequestResult:
    """비교 요청 결과. is_reused면 기존 결과를 돌려준 것."""

    comparison: Comparison
    is_reused: bool


def compute_input_hash(perf: sources.PerformanceSource, stem: Stem, settings: Settings) -> str:
    """연주 파일 + 기준 기타 음원 + 설정이 같으면 같은 결과가 나온다."""
    payload = {
        "workspace_id": perf.workspace_id,
        "performance_id": perf.performance_id,
        "performance_file": perf.file_hash,
        "stem_input_hash": stem.input_hash,
        "config_version": settings.ai_config_version,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _lock_performance(db: Session, performance_id: int) -> None:
    """같은 연주의 비교 요청을 한 줄로 세운다. 트랜잭션이 끝나면 풀린다."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :obj)"),
        {"cls": _LOCK_CLASS_COMPARISONS, "obj": performance_id},
    )


def _default_enqueue(comparison_id: int, start: ComparisonStage) -> None:
    """Celery chain 등록 (테스트에서 바꿔 끼운다)."""
    from app.domains.compare.tasks import enqueue_pipeline  # 순환 import 방지

    enqueue_pipeline(comparison_id, start)


def _publish(
    db: Session,
    c: Comparison,
    start: ComparisonStage,
    enqueue: Enqueue | None,
    *,
    undo_retry: bool = False,
) -> None:
    """commit 후 작업을 등록한다. 등록이 실패하면 503으로 알린다.

    행은 failed(AI_WORKER_ERROR)로 남겨 진행 중으로 묶이지 않게 하고, 재시도였다면 횟수를 되돌린다.
    등록 직전에 프로세스가 죽는 경우는 expire_if_stale이 queued 대기 시간으로 복구한다.
    """
    try:
        (enqueue or _default_enqueue)(c.id, start)
    except Exception as e:
        logger.exception("comparison %s 작업 등록 실패", c.id)
        c.status = ComparisonStatus.FAILED
        c.current_stage = start
        c.error_code = ErrorCode.AI_WORKER_ERROR.code
        if undo_retry:
            c.retry_count -= 1
        db.commit()
        raise BusinessException(ErrorCode.JOB_ENQUEUE_FAILED) from e


def is_stale(c: Comparison, now: datetime | None = None) -> bool:
    """등록이 유실됐거나 워커가 죽어 더 진행되지 않는 작업인지."""
    settings = get_settings()
    if c.status == ComparisonStatus.QUEUED:
        deadline = c.updated_at + timedelta(seconds=settings.ai_job_queued_stale_sec)
    elif c.status == ComparisonStatus.PROCESSING:
        stage = ComparisonStage(c.current_stage or ComparisonStage.ALIGN)
        deadline = c.updated_at + timedelta(
            seconds=hard_limit_sec(stage, settings) + STALE_MARGIN_SEC
        )
    else:
        return False
    return (now or datetime.now(UTC)) > deadline


def expire_if_stale(c: Comparison) -> bool:
    """멈춘 작업을 AI_WORKER_TIMEOUT 실패로 바꾼다 (재시도 가능). 잠근 행에서 호출한다."""
    if not is_stale(c):
        return False
    logger.warning("comparison %s 멈춘 작업 실패 처리 (status=%s)", c.id, c.status)
    c.status = ComparisonStatus.FAILED
    c.error_code = ErrorCode.AI_WORKER_TIMEOUT.code
    return True


def _select(db: Session, comparison_id: int, *, lock: bool) -> Comparison | None:
    """삭제되지 않은 비교 조회. lock이면 행을 잠그고 최신 값으로 읽는다."""
    stmt = select(Comparison).where(Comparison.id == comparison_id, Comparison.deleted_at.is_(None))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.scalars(stmt).first()


def request_comparison(
    db: Session,
    workspace_id: int,
    performance_id: int,
    *,
    enqueue: Enqueue | None = None,
) -> ComparisonRequestResult:
    """비교 요청.

    연주 당시 범위가 현재 구간 범위와 같고, 그 범위의 기타 음원이 준비돼 있어야 한다.
    """
    settings = get_settings()
    perf = sources.load_performance(db, workspace_id, performance_id)
    seg = stem_sources.load_segment(db, workspace_id, perf.segment_id)
    if (perf.start_ms, perf.end_ms) != (seg.start_ms, seg.end_ms):
        raise BusinessException(ErrorCode.SEGMENT_RANGE_MISMATCH)

    stem = stem_service.get_current_stem(db, seg.segment_id, seg.start_ms, seg.end_ms)
    if stem is None or stem.status not in READY_STATUSES or stem.storage_key is None:
        raise BusinessException(ErrorCode.STEM_NOT_READY)

    _lock_performance(db, performance_id)
    input_hash = compute_input_hash(perf, stem, settings)
    existing = db.scalars(
        select(Comparison)
        .where(
            Comparison.input_hash == input_hash,
            Comparison.deleted_at.is_(None),
            Comparison.status.in_([*ACTIVE_STATUSES, *DONE_STATUSES]),
        )
        .order_by(Comparison.id.desc())
        .limit(1)
    ).first()
    if existing is not None and existing.status in ACTIVE_STATUSES:
        locked = _select(db, existing.id, lock=True)
        if locked is None or not expire_if_stale(locked):
            raise BusinessException(ErrorCode.COMPARISON_IN_PROGRESS)
        db.flush()  # 부분 UNIQUE 인덱스에서 빠진 뒤 새 행을 넣는다
        existing = None
    if existing is not None:
        db.rollback()  # 잠금 해제
        return ComparisonRequestResult(existing, is_reused=True)

    c = Comparison(
        workspace_id=perf.workspace_id,
        track_id=perf.track_id,
        performance_id=perf.performance_id,
        segment_id=perf.segment_id,
        stem_id=stem.id,
        tab_id=sources.find_latest_tab_id(db, stem.id),
        start_ms=perf.start_ms,
        end_ms=perf.end_ms,
        status=ComparisonStatus.QUEUED,
        input_hash=input_hash,
        config_version=settings.ai_config_version,
        retry_count=0,
    )
    db.add(c)
    try:
        db.commit()
    except IntegrityError as e:  # 잠금을 거치지 않은 동시 요청의 마지막 방어선
        db.rollback()
        raise BusinessException(ErrorCode.COMPARISON_IN_PROGRESS) from e

    _publish(db, c, ComparisonStage.ALIGN, enqueue)
    return ComparisonRequestResult(c, is_reused=False)


def get_owned_comparison(
    db: Session, workspace_id: int, comparison_id: int, *, lock: bool = False
) -> Comparison:
    """다른 워크스페이스 소유면 없는 것처럼 404. 멈춘 작업은 여기서 실패로 바꾼다."""
    c = _select(db, comparison_id, lock=lock)
    if c is None or c.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.COMPARISON_NOT_FOUND)
    if is_stale(c):
        locked = c if lock else _select(db, comparison_id, lock=True)
        if locked is not None and expire_if_stale(locked):
            db.commit()
            c = _select(db, comparison_id, lock=lock) or locked
    return c


def _error_message(code: str) -> str:
    """error_code에 맞는 사용자 안내 문구."""
    try:
        return ErrorCode[code].message
    except KeyError:
        return ErrorCode.AI_WORKER_ERROR.message


def is_retryable(c: Comparison) -> bool:
    """재시도 버튼을 보여 줄 수 있는지."""
    return (
        c.status == ComparisonStatus.FAILED
        and c.error_code not in NON_RETRYABLE_CODES
        and c.retry_count < MAX_RETRIES
    )


def to_created_response(result: ComparisonRequestResult) -> ComparisonCreatedResponse:
    """비교 요청 응답으로 변환."""
    c = result.comparison
    return ComparisonCreatedResponse(
        comparison_id=c.id,
        performance_id=c.performance_id,
        segment_id=c.segment_id,
        status=c.status,
        is_reused=result.is_reused,
        created_at=c.created_at,
    )


def to_status_response(db: Session, c: Comparison) -> ComparisonResponse:
    """GET /comparisons/{comparison_id} 응답으로 변환."""
    rows = db.scalars(
        select(ComparisonStageResult)
        .where(ComparisonStageResult.comparison_id == c.id)
        .order_by(ComparisonStageResult.stage, ComparisonStageResult.attempt)
    ).all()
    latest = {r.stage: r for r in rows}  # attempt 오름차순이므로 마지막이 최신
    stages = [
        StageView(stage=s, status=latest[s].status if s in latest else StageStatus.QUEUED)
        for s in STAGE_ORDER
    ]
    failure = None
    if c.status == ComparisonStatus.FAILED and c.error_code:
        failure = FailureView(
            stage=c.current_stage,
            error_code=c.error_code,
            message=_error_message(c.error_code),
            retryable=is_retryable(c),
            retry_count=c.retry_count,
            max_retries=MAX_RETRIES,
            retries_left=max(MAX_RETRIES - c.retry_count, 0),
        )
    return ComparisonResponse(
        comparison_id=c.id,
        status=c.status,
        current_stage=c.current_stage,
        not_comparable_reason=c.not_comparable_reason,
        stages=stages,
        failure=failure,
        updated_at=c.updated_at,
    )


def _segment_name(db: Session, c: Comparison) -> str | None:
    """현재 구간 이름 (SEG 연결 전이면 None)."""
    try:
        return stem_sources.load_segment(db, c.workspace_id, c.segment_id).name
    except BusinessException:
        return None


def get_result(db: Session, workspace_id: int, comparison_id: int) -> ComparisonResultResponse:
    """비교 결과. 처리 중이거나 실패한 비교면 409 COMPARISON_NOT_READY."""
    c = get_owned_comparison(db, workspace_id, comparison_id)
    if c.status not in DONE_STATUSES:
        raise BusinessException(ErrorCode.COMPARISON_NOT_READY)

    stem = db.get(Stem, c.stem_id)
    overall: OverallTempoView | None = None
    if c.result_key:
        try:
            body = json.loads(storage.read_bytes(c.result_key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise  # 객체 없음이 아닌 저장소 오류는 그대로 올린다
            logger.warning("comparison %s result.json 없음 — overall_tempo 없이 응답", c.id)
            body = {}
        if body.get("overall_tempo"):
            overall = OverallTempoView(**body["overall_tempo"])

    regions = db.scalars(
        select(ComparisonRegion)
        .where(ComparisonRegion.comparison_id == c.id)
        .order_by(ComparisonRegion.start_ms, ComparisonRegion.id)
    ).all()
    unavailable = sum(1 for r in regions if r.type == RegionType.UNAVAILABLE)
    return ComparisonResultResponse(
        comparison_id=c.id,
        status=c.status,
        is_draft=True,  # 정답 판정이 아니다 (COPY-04)
        not_comparable_reason=c.not_comparable_reason,
        baseline=BaselineView(
            source="separated_input" if stem and stem.bypass_separation else "original_song",
            stem_id=c.stem_id,
            tab_id=c.tab_id,
            segment=SegmentView(
                segment_id=c.segment_id,
                name=_segment_name(db, c),
                start_ms=c.start_ms,
                end_ms=c.end_ms,
            ),
        ),
        overall_tempo=overall,
        summary=SummaryView(
            difference_count=len(regions) - unavailable, unavailable_count=unavailable
        ),
        regions=[
            RegionView(
                region_id=r.id,
                type=r.type,
                start_ms=r.start_ms,
                end_ms=r.end_ms,
                performance_start_ms=r.performance_start_ms,
                performance_end_ms=r.performance_end_ms,
                reason=r.reason,
                is_checked=r.is_checked,
            )
            for r in regions
        ],
        config_version=c.config_version,
    )


def _track(kind: str, label: str, key: str | None, offset_ms: int) -> PlaybackTrackView:
    """재생 대상 하나. 파일이 없으면 available=false."""
    url = storage.presigned_download_url(key) if key else None
    return PlaybackTrackView(
        type=kind, label=label, available=url is not None, url=url, offset_ms=offset_ms
    )


def get_playback(db: Session, workspace_id: int, comparison_id: int) -> PlaybackResponse:
    """원곡·기타 음원·내 연주 재생 URL. 하나라도 없으면 is_partial (CMP-04)."""
    c = get_owned_comparison(db, workspace_id, comparison_id)
    if c.status not in DONE_STATUSES:
        raise BusinessException(ErrorCode.COMPARISON_NOT_READY)

    try:
        original_key: str | None = stem_sources.load_track_storage_key(db, c.track_id)
    except (NotImplementedError, BusinessException):
        original_key = None
    try:
        perf_key: str | None = sources.load_performance(
            db, workspace_id, c.performance_id
        ).storage_key
    except BusinessException:
        perf_key = None
    stem = db.get(Stem, c.stem_id)

    tracks = [
        _track("original", "원곡", original_key, c.start_ms),  # 원곡 파일 안의 구간 시작 위치
        _track("stem", "분리 기타", stem.storage_key if stem else None, 0),
        _track("performance", "내 연주", perf_key, 0),
    ]
    expires = datetime.now(UTC) + timedelta(seconds=get_settings().s3_presigned_expires_seconds)
    return PlaybackResponse(
        comparison_id=c.id,
        is_partial=not all(t.available for t in tracks),
        tracks=tracks,
        tab_id=c.tab_id,
        expires_at=expires,
    )


def check_region(
    db: Session, workspace_id: int, comparison_id: int, region_id: int, is_checked: bool
) -> RegionCheckResponse:
    """다시 들어 볼 구간의 확인 여부를 저장한다."""
    c = get_owned_comparison(db, workspace_id, comparison_id)
    region = db.scalars(
        select(ComparisonRegion).where(
            ComparisonRegion.id == region_id, ComparisonRegion.comparison_id == c.id
        )
    ).first()
    if region is None:
        raise BusinessException(ErrorCode.REGION_NOT_FOUND)
    region.is_checked = is_checked
    db.commit()
    return RegionCheckResponse(region_id=region.id, is_checked=region.is_checked)


def retry_comparison(
    db: Session, workspace_id: int, comparison_id: int, *, enqueue: Enqueue | None = None
) -> RetryResponse:
    """실패한 단계부터 다시 실행한다. 연주를 바꾸려면 REC 재등록 후 새로 비교한다."""
    c = get_owned_comparison(db, workspace_id, comparison_id, lock=True)
    if c.status != ComparisonStatus.FAILED or c.error_code in NON_RETRYABLE_CODES:
        raise BusinessException(ErrorCode.COMPARISON_NOT_RETRYABLE)
    if c.retry_count >= MAX_RETRIES:
        raise BusinessException(ErrorCode.RETRY_LIMIT_EXCEEDED)

    retry_from = ComparisonStage(c.current_stage or ComparisonStage.ALIGN)
    c.retry_count += 1
    c.status = ComparisonStatus.QUEUED
    c.error_code = None
    try:
        db.commit()
    except IntegrityError as e:  # 같은 입력으로 새로 만든 비교가 진행 중
        db.rollback()
        raise BusinessException(ErrorCode.COMPARISON_IN_PROGRESS) from e

    _publish(db, c, retry_from, enqueue, undo_retry=True)
    return RetryResponse(
        comparison_id=c.id,
        status=c.status,
        retry_from=retry_from,
        retry_count=c.retry_count,
        max_retries=MAX_RETRIES,
        retries_left=MAX_RETRIES - c.retry_count,
    )
