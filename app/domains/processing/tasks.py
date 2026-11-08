"""분리·처리 Celery 작업. preprocess(cpu) → separate(gpu).

작업은 stem_id만 받고, 입력·출력 파일은 저장소 key로 주고받는다.
AI 라이브러리는 pipeline 함수 안에서 지연 import 한다 (api·CI에는 AI 패키지가 없음).
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
        self.error_code = error_code
        super().__init__(detail or error_code.message)


@dataclass(frozen=True)
class StageOutcome:
    output_key: str | None
    model: str | None = None
    review_reason: str | None = None  # 마지막 단계에서만 의미 있음 (needs_review)
    skipped: bool = False


StageWork = Callable[[Session, Stem, Path], StageOutcome]

LAST_STAGE = StemStage.SEPARATION


def _now() -> datetime:
    return datetime.now(UTC)


def _should_skip(db: Session, stem: Stem, stage: StemStage) -> bool:
    """같은 작업이 다시 전달되거나(acks_late), 중단·완료·실패한 작업이면 건너뛴다."""
    if stem.deleted_at is not None:
        return True
    if stem.status in (StemStatus.CANCELED, StemStatus.FAILED, *READY_STATUSES):
        return True
    last = db.scalars(
        select(StemStageResult)
        .where(StemStageResult.stem_id == stem.id, StemStageResult.stage == stage)
        .order_by(StemStageResult.attempt.desc())
        .limit(1)
    ).first()
    return last is not None and last.status in (StageStatus.SUCCESS, StageStatus.SKIPPED)


def run_stage(
    stem_id: int,
    stage: StemStage,
    work: StageWork,
    session_factory: Callable[[], Session] = SessionLocal,
) -> bool:
    """한 단계를 실행하고 결과를 기록한다. 실패하면 False (chain을 멈춘다)."""
    with session_factory() as db:
        stem = db.scalars(
            select(Stem)
            .where(Stem.id == stem_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if stem is None:
            logger.warning("stem %s 없음 — 건너뜀", stem_id)
            return False
        if _should_skip(db, stem, stage):
            logger.info("stem %s %s 건너뜀 (status=%s)", stem_id, stage, stem.status)
            return stem.status not in (StemStatus.CANCELED, StemStatus.FAILED)

        attempt = (
            db.scalar(
                select(func.max(StemStageResult.attempt)).where(
                    StemStageResult.stem_id == stem.id, StemStageResult.stage == stage
                )
            )
            or 0
        ) + 1
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

        result.finished_at = _now()
        if error is not None or outcome is None:
            code = (error or ErrorCode.AI_WORKER_ERROR).code
            result.status = StageStatus.FAILED
            result.error_code = code
            stem.status = StemStatus.FAILED
            stem.error_code = code
            db.commit()
            return False

        result.status = StageStatus.SKIPPED if outcome.skipped else StageStatus.SUCCESS
        result.output_key = outcome.output_key
        result.model = outcome.model
        if stage == LAST_STAGE:
            stem.storage_key = outcome.output_key
            stem.review_reason = outcome.review_reason
            stem.status = StemStatus.NEEDS_REVIEW if outcome.review_reason else StemStatus.SUCCESS
        db.commit()
        return True


# ---- 단계별 작업 ----


def preprocess_work(db: Session, stem: Stem, tmp: Path) -> StageOutcome:
    from app.domains.processing.pipeline.preprocess import InvalidAudioError, preprocess

    src = tmp / "source"
    out = tmp / "input.wav"
    storage.download_file(sources.load_track_storage_key(db, stem.track_id), str(src))
    try:
        preprocess(
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
    return StageOutcome(output_key=key, model="ffmpeg")


def separate_work(db: Session, stem: Stem, tmp: Path) -> StageOutcome:
    from app.domains.processing.pipeline import separation

    src = tmp / "input.wav"
    out = tmp / "guitar.wav"
    storage.download_file(keys.input_key(stem), str(src))
    pad_before_ms = min(settings.ai_preprocess_padding_ms, stem.start_ms)
    duration_ms = stem.end_ms - stem.start_ms

    if stem.bypass_separation:
        separation.trim(str(src), str(out), pad_before_ms, duration_ms)
        model = None
    else:
        try:
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
        except separation.NoGuitarError as e:
            raise StageError(ErrorCode.SEPARATION_NO_GUITAR, str(e)) from e
        model = res.model

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
    soft_time_limit=settings.ai_preprocess_timeout_sec,
    time_limit=settings.ai_preprocess_timeout_sec + 30,
)
def preprocess(stem_id: int) -> int:
    if not run_stage(stem_id, StemStage.PREPROCESS, preprocess_work):
        raise Ignore()  # 실패·중단이면 다음 단계(chain)를 실행하지 않는다
    return stem_id


@celery_app.task(
    name="processing.separate",
    queue="gpu",
    soft_time_limit=settings.ai_separation_timeout_sec,
    time_limit=settings.ai_separation_timeout_sec + 60,
)
def separate(stem_id: int) -> int:
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
