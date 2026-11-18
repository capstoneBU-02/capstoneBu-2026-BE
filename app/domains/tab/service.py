"""타브(TAB) 비즈니스 로직. 트랜잭션(commit)은 여기서만 한다.

생성·재사용·재시도·멈춘 작업 복구는 JOB(processing.service)과 같은 규칙을 쓴다.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.core.locks import LockClass
from app.domains.processing import service as processing_service
from app.domains.processing import sources
from app.domains.processing.models import READY_STATUSES as STEM_READY_STATUSES
from app.domains.processing.models import Stem, StemStatus
from app.domains.processing.schemas import FailureView, StageView
from app.domains.tab import conditions, keys
from app.domains.tab.limits import hard_limit_sec
from app.domains.tab.models import (
    ACTIVE_STATUSES,
    DONE_STATUSES,
    STAGE_ORDER,
    ReviewReason,
    StageStatus,
    Tab,
    TabStage,
    TabStageResult,
    TabStatus,
)
from app.domains.tab.schemas import (
    CapoRange,
    ConditionDefaults,
    ConditionsResponse,
    ConditionView,
    ModelView,
    ResultNote,
    ResultSummary,
    ResultTypeView,
    SegmentView,
    TabCreatedResponse,
    TabCreateRequest,
    TabDownloadResponse,
    TabListItem,
    TabListResponse,
    TabOutput,
    TabResponse,
    TabResultResponse,
    TabRetryResponse,
    TuningView,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
STALE_MARGIN_SEC = 60  # processing 상태가 강제 종료 시간보다 이만큼 더 지나면 멈춘 작업
NON_RETRYABLE_CODES = frozenset({ErrorCode.STEM_MISSING.code})
DOWNLOAD_FORMATS = ("musicxml",)

# DB 잠금 첫 번째 키 (app/core/locks.py에서 한곳에서 정함)
_LOCK_CLASS_TABS = LockClass.TABS

Enqueue = Callable[[int, TabStage], None]


@dataclass(frozen=True)
class TabRequestResult:
    tab: Tab
    is_reused: bool


def supported_conditions() -> ConditionsResponse:
    return ConditionsResponse(
        tunings=[
            TuningView(id=t.id, name=t.name, strings=len(t.notes), notes=list(t.notes))
            for t in conditions.TUNINGS.values()
        ],
        capo=CapoRange(min=conditions.CAPO_RANGE[0], max=conditions.CAPO_RANGE[1]),
        result_types=[ResultTypeView(id=k, name=v) for k, v in conditions.RESULT_TYPES.items()],
        default=ConditionDefaults(
            result_type=conditions.DEFAULT_RESULT_TYPE,
            tuning_id=conditions.DEFAULT_TUNING_ID,
            capo=conditions.DEFAULT_CAPO,
        ),
    )


def compute_input_hash(stem: Stem, req: TabCreateRequest, settings: Settings) -> str:
    """기타 음원·결과 유형·연주 조건·설정 버전이 같으면 같은 타브가 나온다."""
    payload = {
        "stem_input_hash": stem.input_hash,
        "result_type": req.result_type,
        "tuning_id": req.tuning_id,
        "capo": req.capo,
        "config_version": settings.ai_config_version,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _default_enqueue(tab_id: int, start: TabStage) -> None:
    """Celery chain 등록 (테스트에서 바꿔 끼운다)."""
    from app.domains.tab.tasks import enqueue_pipeline  # 순환 import 방지

    enqueue_pipeline(tab_id, start)


def _publish(
    db: Session, tab: Tab, start: TabStage, enqueue: Enqueue | None, *, undo_retry: bool = False
) -> None:
    """commit 후 작업을 등록한다. 실패하면 행을 failed(AI_WORKER_ERROR)로 남기고 503."""
    try:
        (enqueue or _default_enqueue)(tab.id, start)
    except Exception as e:
        logger.exception("tab %s 작업 등록 실패", tab.id)
        tab.status = TabStatus.FAILED
        tab.current_stage = start
        tab.error_code = ErrorCode.AI_WORKER_ERROR.code
        if undo_retry:
            tab.retry_count -= 1
        db.commit()
        raise BusinessException(ErrorCode.JOB_ENQUEUE_FAILED) from e


def is_stale(tab: Tab, now: datetime | None = None) -> bool:
    """등록이 유실됐거나 워커가 죽어 더 진행되지 않는 작업인지."""
    settings = get_settings()
    if tab.status == TabStatus.QUEUED:
        deadline = tab.updated_at + timedelta(seconds=settings.ai_job_queued_stale_sec)
    elif tab.status == TabStatus.PROCESSING:
        stage = TabStage(tab.current_stage or TabStage.TRANSCRIPTION)
        limit = hard_limit_sec(stage, settings) + STALE_MARGIN_SEC
        deadline = tab.updated_at + timedelta(seconds=limit)
    else:
        return False
    return (now or datetime.now(UTC)) > deadline


def expire_if_stale(tab: Tab, now: datetime | None = None) -> bool:
    """멈춘 작업을 AI_WORKER_TIMEOUT 실패로 바꾼다 (재시도 가능). 잠근 행에서 호출한다."""
    if not is_stale(tab, now):
        return False
    logger.warning("tab %s 멈춘 작업 실패 처리 (status=%s)", tab.id, tab.status)
    tab.status = TabStatus.FAILED
    tab.error_code = ErrorCode.AI_WORKER_TIMEOUT.code
    return True


def _select_tab(db: Session, tab_id: int, *, lock: bool) -> Tab | None:
    stmt = select(Tab).where(Tab.id == tab_id, Tab.deleted_at.is_(None))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.scalars(stmt).first()


def request_tab(
    db: Session,
    workspace_id: int,
    segment_id: int,
    req: TabCreateRequest,
    *,
    enqueue: Enqueue | None = None,
) -> TabRequestResult:
    """타브 생성 요청. 같은 조건이 진행 중이면 409, 완료돼 있으면 재사용한다."""
    settings = get_settings()
    src = sources.load_segment(db, workspace_id, segment_id, lock=True)
    if not conditions.is_supported(req.result_type, req.tuning_id, req.capo):
        raise BusinessException(ErrorCode.UNSUPPORTED_CONDITION)
    stem = processing_service.get_current_stem(db, src.segment_id, src.start_ms, src.end_ms)
    if stem is None or stem.status not in STEM_READY_STATUSES or stem.storage_key is None:
        raise BusinessException(ErrorCode.STEM_NOT_READY)

    db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :obj)"),
        {"cls": _LOCK_CLASS_TABS, "obj": segment_id},
    )
    input_hash = compute_input_hash(stem, req, settings)
    existing = db.scalars(
        select(Tab)
        .where(
            Tab.input_hash == input_hash,
            Tab.deleted_at.is_(None),
            Tab.status.in_([*ACTIVE_STATUSES, *DONE_STATUSES]),
        )
        .order_by(Tab.version.desc())
        .limit(1)
    ).first()
    if existing is not None and existing.status in ACTIVE_STATUSES:
        locked = _select_tab(db, existing.id, lock=True)
        if locked is None or not expire_if_stale(locked):
            raise BusinessException(ErrorCode.TAB_IN_PROGRESS)
        db.flush()  # 부분 UNIQUE 인덱스에서 빠진 뒤 새 행을 넣는다
        existing = None
    if existing is not None:
        db.rollback()  # 잠금 해제
        return TabRequestResult(existing, is_reused=True)

    last_version = db.scalar(
        select(func.coalesce(func.max(Tab.version), 0)).where(Tab.segment_id == segment_id)
    )
    reasons = [ReviewReason.STEM_NEEDS_REVIEW] if stem.status == StemStatus.NEEDS_REVIEW else []
    tab = Tab(
        workspace_id=src.workspace_id,
        track_id=src.track_id,
        segment_id=src.segment_id,
        stem_id=stem.id,
        version=int(last_version or 0) + 1,
        result_type=req.result_type,
        tuning_id=req.tuning_id,
        capo=req.capo,
        status=TabStatus.QUEUED,
        review_reasons=[str(r) for r in reasons],
        input_hash=input_hash,
        config_version=settings.ai_config_version,
        retry_count=0,
    )
    db.add(tab)
    try:
        db.commit()
    except IntegrityError as e:  # 잠금을 거치지 않은 동시 요청의 마지막 방어선
        db.rollback()
        raise BusinessException(ErrorCode.TAB_IN_PROGRESS) from e

    _publish(db, tab, TabStage.TRANSCRIPTION, enqueue)
    return TabRequestResult(tab, is_reused=False)


def get_owned_tab(db: Session, workspace_id: int, tab_id: int, *, lock: bool = False) -> Tab:
    """다른 워크스페이스 소유면 없는 것처럼 404. 멈춘 작업은 여기서 실패로 바꾼다."""
    tab = _select_tab(db, tab_id, lock=lock)
    if tab is None or tab.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.TAB_NOT_FOUND)
    if is_stale(tab):
        locked = tab if lock else _select_tab(db, tab_id, lock=True)
        if locked is not None and expire_if_stale(locked):
            db.commit()
            tab = _select_tab(db, tab_id, lock=lock) or locked
    return tab


def list_tabs(db: Session, workspace_id: int, segment_id: int) -> TabListResponse:
    """같은 구간의 타브를 최신 version부터. 첫 항목이 is_latest."""
    sources.load_segment(db, workspace_id, segment_id)  # 소유권 확인
    tabs = db.scalars(
        select(Tab)
        .where(Tab.segment_id == segment_id, Tab.deleted_at.is_(None))
        .order_by(Tab.version.desc())
    ).all()
    return TabListResponse(
        items=[
            TabListItem(
                tab_id=t.id,
                stem_id=t.stem_id,
                version=t.version,
                is_latest=i == 0,
                result_type=t.result_type,
                status=t.status,
                tuning_id=t.tuning_id,
                capo=t.capo,
                created_at=t.created_at,
            )
            for i, t in enumerate(tabs)
        ]
    )


def is_retryable(tab: Tab) -> bool:
    return (
        tab.status == TabStatus.FAILED
        and tab.error_code not in NON_RETRYABLE_CODES
        and tab.retry_count < MAX_RETRIES
    )


def _error_message(code: str) -> str:
    try:
        return ErrorCode[code].message
    except KeyError:
        return ErrorCode.AI_WORKER_ERROR.message


def _latest_stage_results(db: Session, tab_id: int) -> dict[str, TabStageResult]:
    rows = db.scalars(
        select(TabStageResult)
        .where(TabStageResult.tab_id == tab_id)
        .order_by(TabStageResult.stage, TabStageResult.attempt)
    ).all()
    return {row.stage: row for row in rows}  # attempt 오름차순이므로 마지막이 최신


def to_tab_response(db: Session, tab: Tab) -> TabResponse:
    latest = _latest_stage_results(db, tab.id)
    stages = [
        StageView(stage=s, status=latest[s].status if s in latest else StageStatus.QUEUED)
        for s in STAGE_ORDER
    ]
    failure = None
    if tab.status == TabStatus.FAILED and tab.error_code:
        failure = FailureView(
            stage=tab.current_stage,
            error_code=tab.error_code,
            message=_error_message(tab.error_code),
            retryable=is_retryable(tab),
            retry_count=tab.retry_count,
            max_retries=MAX_RETRIES,
            retries_left=max(MAX_RETRIES - tab.retry_count, 0),
        )
    return TabResponse(
        tab_id=tab.id,
        segment_id=tab.segment_id,
        version=tab.version,
        status=tab.status,
        current_stage=tab.current_stage,
        stages=stages,
        failure=failure,
        updated_at=tab.updated_at,
    )


def to_created_response(result: TabRequestResult) -> TabCreatedResponse:
    t = result.tab
    return TabCreatedResponse(
        tab_id=t.id,
        segment_id=t.segment_id,
        stem_id=t.stem_id,
        version=t.version,
        status=t.status,
        is_reused=result.is_reused,
        created_at=t.created_at,
    )


def _get_done_tab(db: Session, workspace_id: int, tab_id: int) -> Tab:
    tab = get_owned_tab(db, workspace_id, tab_id)
    if tab.status not in DONE_STATUSES:
        raise BusinessException(ErrorCode.TAB_NOT_READY)
    return tab


def _expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=get_settings().s3_presigned_expires_seconds)


def get_tab_result(db: Session, workspace_id: int, tab_id: int) -> TabResultResponse:
    """화면에 타브를 그리는 데 필요한 결과. success·needs_review일 때만."""
    tab = _get_done_tab(db, workspace_id, tab_id)
    seg = sources.load_segment(db, workspace_id, tab.segment_id)
    output = TabOutput.model_validate_json(storage.read_bytes(keys.tab_json_key(tab)))
    condition = conditions.playing_condition(tab.tuning_id, tab.capo)
    settings = get_settings()
    return TabResultResponse(
        tab_id=tab.id,
        stem_id=tab.stem_id,
        version=tab.version,
        result_type=tab.result_type,
        status=tab.status,
        segment=SegmentView(
            segment_id=seg.segment_id, name=seg.name, start_ms=seg.start_ms, end_ms=seg.end_ms
        ),
        condition=ConditionView(
            tuning_id=tab.tuning_id,
            tuning_notes=list(condition.tuning),
            strings=condition.strings,
            capo=tab.capo,
        ),
        tempo_bpm=output.tempo_bpm,
        time_signature=output.time_signature,
        tempo_uncertain=output.tempo_uncertain,
        review_reasons=list(tab.review_reasons),
        summary=ResultSummary(
            note_count=len(output.notes),
            low_confidence_count=sum(n.low_confidence for n in output.notes),
            unplayable_count=sum(not n.playable for n in output.notes),
        ),
        notes=[
            ResultNote(**n.model_dump(exclude={"velocity", "bend"}), bend=list(n.bend))
            for n in output.notes
        ],
        musicxml_url=storage.presigned_download_url(keys.musicxml_key(tab)),
        musicxml_url_expires_at=_expires_at(),
        model=ModelView(
            config_version=tab.config_version,
            transcription=f"basic-pitch {settings.ai_transcription_weights}",
            beat=f"beat-this {settings.ai_beat_model}",
        ),
    )


def retry_tab(
    db: Session, workspace_id: int, tab_id: int, *, enqueue: Enqueue | None = None
) -> TabRetryResponse:
    """실패한 단계부터 다시 실행한다. 같은 tab_id라 version·stem_id는 그대로."""
    tab = get_owned_tab(db, workspace_id, tab_id, lock=True)
    if tab.status != TabStatus.FAILED or tab.error_code in NON_RETRYABLE_CODES:
        raise BusinessException(ErrorCode.TAB_NOT_RETRYABLE)
    if tab.retry_count >= MAX_RETRIES:
        raise BusinessException(ErrorCode.RETRY_LIMIT_EXCEEDED)

    retry_from = TabStage(tab.current_stage or TabStage.TRANSCRIPTION)
    tab.retry_count += 1
    tab.status = TabStatus.QUEUED
    tab.error_code = None
    try:
        db.commit()
    except IntegrityError as e:  # 같은 조건으로 새로 만든 작업이 진행 중
        db.rollback()
        raise BusinessException(ErrorCode.TAB_IN_PROGRESS) from e

    _publish(db, tab, retry_from, enqueue, undo_retry=True)
    return TabRetryResponse(
        tab_id=tab.id,
        status=tab.status,
        retry_from=retry_from,
        retry_count=tab.retry_count,
        max_retries=MAX_RETRIES,
        retries_left=MAX_RETRIES - tab.retry_count,
    )


def get_download(
    db: Session, workspace_id: int, tab_id: int, file_format: str
) -> TabDownloadResponse:
    """MusicXML 다운로드 URL. PDF는 프론트가 alphaTab 화면에서 만든다 (D13)."""
    if file_format not in DOWNLOAD_FORMATS:
        raise BusinessException(ErrorCode.UNSUPPORTED_DOWNLOAD_FORMAT)
    tab = _get_done_tab(db, workspace_id, tab_id)
    seg = sources.load_segment(db, workspace_id, tab.segment_id)
    tuning = tab.tuning_id.replace("_", "-")
    file_name = f"{seg.name or 'SEGMENT'}_v{tab.version}_{tuning}.musicxml"
    return TabDownloadResponse(
        format=file_format,
        file_name=file_name,
        url=storage.presigned_download_url(keys.musicxml_key(tab), download_name=file_name),
        expires_at=_expires_at(),
    )
