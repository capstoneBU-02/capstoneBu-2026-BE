"""분리·처리 Celery 작업. preprocess(cpu) → separate(gpu).

작업은 stem_id만 받고, 입력·출력 파일은 저장소 key로 주고받는다.
AI 라이브러리는 pipeline 함수 안에서 지연 import 한다 (api·CI에는 AI 패키지가 없음).
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from celery import chain
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import ErrorCode
from app.domains.processing import keys, sources
from app.domains.processing.limits import hard_limit_sec, soft_limit_sec
from app.domains.processing.models import (
    READY_STATUSES,
    StageStatus,
    Stem,
    StemStage,
    StemStageResult,
    StemStatus,
)

logger = logging.getLogger(__name__)
settings = get_settings()

WAV = "audio/wav"


class StageError(Exception):
    """pipeline이 알려 주는 예상된 실패. error_code로 사용자에게 안내한다."""

    def __init__(self, error_code: ErrorCode, detail: str | None = None) -> None:
        """error_code와 상세 설명(로그용)을 받는다."""
        self.error_code = error_code
        super().__init__(detail or error_code.message)


@dataclass(frozen=True)
class StageOutcome:
    """단계 실행 결과. run_stage가 stem과 단계 결과에 반영한다."""

    output_key: str | None
    model: str | None = None
    review_reason: str | None = None  # 마지막 단계에서만 의미 있음 (needs_review)
    skipped: bool = False
    input_pad_ms: int | None = None  # preprocess만: 실제로 넣은 앞쪽 여유


StageWork = Callable[[Session, Stem, Path], StageOutcome]

LAST_STAGE = StemStage.SEPARATION


def _now() -> datetime:
    """UTC 현재 시각."""
    return datetime.now(UTC)


def _lock_stem(db: Session, stem_id: int) -> Stem | None:
    """stem 행을 잠그고 DB의 최신 값으로 읽는다."""
    return db.scalars(
        select(Stem)
        .where(Stem.id == stem_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _latest_attempt(db: Session, stem_id: int, stage: StemStage) -> StemStageResult | None:
    """같은 단계의 마지막 시도."""
    return db.scalars(
        select(StemStageResult)
        .where(StemStageResult.stem_id == stem_id, StemStageResult.stage == stage)
        .order_by(StemStageResult.attempt.desc())
        .limit(1)
    ).first()


def _skip_reason(db: Session, stem: Stem, stage: StemStage) -> str | None:
    """실행하지 않을 이유. None이면 실행한다.

    - 중단·실패·완료된 작업
    - 같은 단계가 이미 끝남 (acks_late로 같은 작업이 다시 전달된 경우)
    - 같은 단계가 다른 워커에서 아직 실행 중 (제한 시간이 지나면 멈춘 것으로 보고 이어받음)
    """
    if stem.deleted_at is not None:
        return "deleted"
    if stem.status in (StemStatus.CANCELED, StemStatus.FAILED, *READY_STATUSES):
        return f"status={stem.status}"
    last = _latest_attempt(db, stem.id, stage)
    if last is None:
        return None
    if last.status in (StageStatus.SUCCESS, StageStatus.SKIPPED):
        return "stage done"
    if last.status == StageStatus.PROCESSING and last.started_at is not None:
        deadline = last.started_at + timedelta(seconds=hard_limit_sec(stage, settings))
        if _now() < deadline:
            return "stage running elsewhere"
    return None


def run_stage(
    stem_id: int,
    stage: StemStage,
    work: StageWork,
    session_factory: Callable[[], Session] = SessionLocal,
) -> bool:
    """한 단계를 실행하고 결과를 기록한다. 다음 단계로 진행할 수 있으면 True."""
    with session_factory() as db:
        stem = _lock_stem(db, stem_id)
        if stem is None:
            logger.warning("stem %s 없음 — 건너뜀", stem_id)
            return False
        reason = _skip_reason(db, stem, stage)
        if reason is not None:
            logger.info("stem %s %s 건너뜀 (%s)", stem_id, stage, reason)
            return reason == "stage done"

        last = _latest_attempt(db, stem.id, stage)
        attempt = (last.attempt if last else 0) + 1
        result = StemStageResult(
            stem_id=stem.id,
            stage=stage,
            status=StageStatus.PROCESSING,
            attempt=attempt,
            started_at=_now(),
        )
        db.add(result)
        stem.status = StemStatus.PROCESSING
        stem.current_stage = stage
        db.commit()

        error: ErrorCode | None = None
        outcome: StageOutcome | None = None
        try:
            with tempfile.TemporaryDirectory(prefix=f"stem{stem_id}-") as tmp:
                outcome = work(db, stem, Path(tmp))
        except StageError as e:
            error = e.error_code
        except SoftTimeLimitExceeded:
            error = ErrorCode.AI_WORKER_TIMEOUT
        except Exception:
            logger.exception("stem %s %s 처리 중 오류", stem_id, stage)
            error = ErrorCode.AI_WORKER_ERROR

        # 결과는 다시 잠근 뒤, 이 시도가 아직 최신일 때만 stem에 반영한다
        # 작업 중 DB 오류로 트랜잭션이 깨졌을 수 있으므로 정리한 뒤 다시 잠근다
        db.rollback()
        locked = _lock_stem(db, stem_id)
        newest = db.scalar(
            select(func.max(StemStageResult.attempt)).where(
                StemStageResult.stem_id == stem_id, StemStageResult.stage == stage
            )
        )
        owns = (
            locked is not None
            and newest == attempt
            and locked.status == StemStatus.PROCESSING
            and locked.current_stage == stage
        )
        result.finished_at = _now()

        if error is not None or outcome is None:
            code = (error or ErrorCode.AI_WORKER_ERROR).code
            result.status = StageStatus.FAILED
            result.error_code = code
            if owns and locked is not None:
                locked.status = StemStatus.FAILED
                locked.error_code = code
            db.commit()
            return False

        result.status = StageStatus.SKIPPED if outcome.skipped else StageStatus.SUCCESS
        result.output_key = outcome.output_key
        result.model = outcome.model
        if not owns or locked is None:
            logger.warning(
                "stem %s %s attempt %s: 결과만 기록 (최신 시도 아님)", stem_id, stage, attempt
            )
            db.commit()
            return False
        if outcome.input_pad_ms is not None:
            locked.input_pad_ms = outcome.input_pad_ms
        if stage == LAST_STAGE:
            locked.storage_key = outcome.output_key
            locked.review_reason = outcome.review_reason
            locked.status = StemStatus.NEEDS_REVIEW if outcome.review_reason else StemStatus.SUCCESS
        else:
            # 다음 단계를 큐에서 기다리는 동안은 queued (대기 시간을 실행 시간으로 세지 않는다)
            locked.status = StemStatus.QUEUED
        db.commit()
        return True


# ---- 단계별 작업 ----


def preprocess_work(db: Session, stem: Stem, tmp: Path) -> StageOutcome:
    """원본을 받아 구간 WAV(앞뒤 여유 포함)를 만들어 올린다."""
    from app.domains.processing.pipeline.preprocess import InvalidAudioError, preprocess

    src = tmp / "source"
    out = tmp / "input.wav"
    storage.download_file(sources.load_track_storage_key(db, stem.track_id), str(src))
    try:
        res = preprocess(
            str(src),
            stem.start_ms,
            stem.end_ms,
            str(out),
            padding_ms=settings.ai_preprocess_padding_ms,
            sample_rate=settings.ai_sample_rate,
            silence_dbfs=settings.ai_silence_dbfs,
        )
    except InvalidAudioError as e:
        raise StageError(ErrorCode.PREPROCESS_INVALID_AUDIO, str(e)) from e
    key = keys.input_key(stem)
    storage.upload_file(str(out), key, WAV)
    return StageOutcome(output_key=key, model="ffmpeg", input_pad_ms=res.pad_before_ms)


def separate_work(db: Session, stem: Stem, tmp: Path) -> StageOutcome:
    """구간 WAV에서 기타를 분리해 구간 길이로 자른 뒤 올린다. 분리 음원이면 자르기만 한다."""
    from app.domains.processing.pipeline import separation

    src = tmp / "input.wav"
    out = tmp / "guitar.wav"
    storage.download_file(keys.input_key(stem), str(src))
    # 설정값이 그 사이 바뀌어도 preprocess가 실제로 넣은 여유로 자른다
    pad_before_ms = (
        stem.input_pad_ms
        if stem.input_pad_ms is not None
        else min(settings.ai_preprocess_padding_ms, stem.start_ms)
    )
    duration_ms = stem.end_ms - stem.start_ms

    try:
        if stem.bypass_separation:
            separation.trim(str(src), str(out), pad_before_ms, duration_ms)
            model = None
        else:
            res = separation.separate_guitar(
                str(src),
                str(out),
                model=settings.ai_separation_model,
                model_dir=str(Path(settings.ai_model_cache_dir) / "audio-separator"),
                trim_start_ms=pad_before_ms,
                duration_ms=duration_ms,
                no_guitar_db=settings.ai_separation_rms_db,
                fallback_model=settings.ai_separation_fallback,
            )
            model = res.model
    except separation.NoGuitarError as e:
        raise StageError(ErrorCode.SEPARATION_NO_GUITAR, str(e)) from e
    except separation.ShortAudioError as e:
        # 구간이 음원 길이를 넘는다. 다시 실행해도 같으므로 입력 오류로 처리한다
        raise StageError(ErrorCode.PREPROCESS_INVALID_AUDIO, str(e)) from e

    ratio = separation.active_ratio(str(out), silence_dbfs=settings.ai_silence_dbfs)
    review = (
        ErrorCode.SEPARATION_UNCLEAR.code
        if ratio < settings.ai_separation_min_active_ratio
        else None
    )
    key = keys.guitar_key(stem)
    storage.upload_file(str(out), key, WAV)
    return StageOutcome(
        output_key=key, model=model, review_reason=review, skipped=stem.bypass_separation
    )


# ---- Celery 작업 ----


@celery_app.task(
    name="processing.preprocess",
    queue="cpu",
    soft_time_limit=soft_limit_sec(StemStage.PREPROCESS, settings),
    time_limit=hard_limit_sec(StemStage.PREPROCESS, settings),
)
def preprocess(stem_id: int) -> int:
    """구간 자르기·WAV 변환·무음 검사."""
    if not run_stage(stem_id, StemStage.PREPROCESS, preprocess_work):
        raise Ignore()  # 실패·중단이면 다음 단계(chain)를 실행하지 않는다
    return stem_id


@celery_app.task(
    name="processing.separate",
    queue="gpu",
    soft_time_limit=soft_limit_sec(StemStage.SEPARATION, settings),
    time_limit=hard_limit_sec(StemStage.SEPARATION, settings),
)
def separate(stem_id: int) -> int:
    """기타 분리 (분리 음원이면 건너뛰고 자르기만)."""
    if not run_stage(stem_id, StemStage.SEPARATION, separate_work):
        raise Ignore()
    return stem_id


def enqueue_pipeline(stem_id: int, start: StemStage) -> None:
    """start 단계부터 끝까지 chain으로 등록한다. 앞 단계 결과는 저장소에 남아 있다."""
    steps = []
    if start == StemStage.PREPROCESS:
        steps.append(preprocess.si(stem_id))
    steps.append(separate.si(stem_id))
    chain(*steps).apply_async()
