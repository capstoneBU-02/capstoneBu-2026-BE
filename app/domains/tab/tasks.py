"""타브 Celery 작업. transcription → rhythm → tab (모두 cpu 큐).

작업은 tab_id만 받고, 단계 결과는 저장소 JSON으로 주고받는다.
단계 실행·재전달·중복 실행 방지 규칙은 JOB(processing.tasks.run_stage)과 같다.
AI 라이브러리는 pipeline 함수 안에서 지연 import 한다 (api·CI에는 AI 패키지가 없음).
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from botocore.exceptions import ClientError
from celery import chain
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import ErrorCode
from app.domains.processing import keys as processing_keys
from app.domains.processing.models import Stem
from app.domains.tab import conditions, keys
from app.domains.tab.limits import hard_limit_sec, soft_limit_sec
from app.domains.tab.models import (
    DONE_STATUSES,
    STAGE_ORDER,
    ReviewReason,
    StageStatus,
    Tab,
    TabStage,
    TabStageResult,
    TabStatus,
)
from app.domains.tab.schemas import Note, RhythmOutput, TabOutput

logger = logging.getLogger(__name__)
settings = get_settings()

JSON = "application/json"
MUSICXML = "application/vnd.recordare.musicxml+xml"
LAST_STAGE = TabStage.TAB
NOTES = TypeAdapter(list[Note])


class StageError(Exception):
    """예상된 실패. error_code로 사용자에게 안내한다."""

    def __init__(self, error_code: ErrorCode, detail: str | None = None) -> None:
        """error_code와 상세 설명(로그용)을 받는다."""
        self.error_code = error_code
        super().__init__(detail or error_code.message)


@dataclass(frozen=True)
class StageOutcome:
    """단계 실행 결과. run_stage가 타브와 단계 결과에 반영한다."""

    output_key: str | None
    model: str | None = None
    review_reasons: list[str] = field(default_factory=list)  # 마지막 단계에서만 의미 있음


StageWork = Callable[[Session, Tab, Path], StageOutcome]


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_tab(db: Session, tab_id: int) -> Tab | None:
    """타브 행을 잠그고 DB의 최신 값으로 읽는다."""
    return db.scalars(
        select(Tab)
        .where(Tab.id == tab_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _latest_attempt(db: Session, tab_id: int, stage: TabStage) -> TabStageResult | None:
    return db.scalars(
        select(TabStageResult)
        .where(TabStageResult.tab_id == tab_id, TabStageResult.stage == stage)
        .order_by(TabStageResult.attempt.desc())
        .limit(1)
    ).first()


def _skip_reason(db: Session, tab: Tab, stage: TabStage) -> str | None:
    """실행하지 않을 이유. None이면 실행한다 (JOB의 run_stage와 같은 규칙)."""
    if tab.deleted_at is not None:
        return "deleted"
    if tab.status in (TabStatus.FAILED, *DONE_STATUSES):
        return f"status={tab.status}"
    last = _latest_attempt(db, tab.id, stage)
    if last is None:
        return None
    if last.status == StageStatus.SUCCESS:
        return "stage done"
    if last.status == StageStatus.PROCESSING and last.started_at is not None:
        deadline = last.started_at + timedelta(seconds=hard_limit_sec(stage, settings))
        if _now() < deadline:
            return "stage running elsewhere"
    return None


def run_stage(
    tab_id: int,
    stage: TabStage,
    work: StageWork,
    session_factory: Callable[[], Session] = SessionLocal,
) -> bool:
    """한 단계를 실행하고 결과를 기록한다. 다음 단계로 진행할 수 있으면 True."""
    with session_factory() as db:
        tab = _lock_tab(db, tab_id)
        if tab is None:
            logger.warning("tab %s 없음 — 건너뜀", tab_id)
            return False
        reason = _skip_reason(db, tab, stage)
        if reason is not None:
            logger.info("tab %s %s 건너뜀 (%s)", tab_id, stage, reason)
            return reason == "stage done"

        last = _latest_attempt(db, tab.id, stage)
        attempt = (last.attempt if last else 0) + 1
        result = TabStageResult(
            tab_id=tab.id,
            stage=stage,
            status=StageStatus.PROCESSING,
            attempt=attempt,
            started_at=_now(),
        )
        db.add(result)
        tab.status = TabStatus.PROCESSING
        tab.current_stage = stage
        db.commit()

        error: ErrorCode | None = None
        outcome: StageOutcome | None = None
        try:
            with tempfile.TemporaryDirectory(prefix=f"tab{tab_id}-") as tmp:
                outcome = work(db, tab, Path(tmp))
        except StageError as e:
            error = e.error_code
        except SoftTimeLimitExceeded:
            error = ErrorCode.AI_WORKER_TIMEOUT
        except Exception:
            logger.exception("tab %s %s 처리 중 오류", tab_id, stage)
            error = ErrorCode.AI_WORKER_ERROR

        # 작업 중 DB 오류로 트랜잭션이 깨졌을 수 있으므로 정리한 뒤,
        # 다시 잠그고 이 시도가 아직 최신일 때만 타브에 반영한다
        db.rollback()
        locked = _lock_tab(db, tab_id)
        newest = db.scalar(
            select(func.max(TabStageResult.attempt)).where(
                TabStageResult.tab_id == tab_id, TabStageResult.stage == stage
            )
        )
        owns = (
            locked is not None
            and newest == attempt
            and locked.status == TabStatus.PROCESSING
            and locked.current_stage == stage
        )
        result.finished_at = _now()

        if error is not None or outcome is None:
            code = (error or ErrorCode.AI_WORKER_ERROR).code
            result.status = StageStatus.FAILED
            result.error_code = code
            if owns and locked is not None:
                locked.status = TabStatus.FAILED
                locked.error_code = code
            db.commit()
            return False

        result.status = StageStatus.SUCCESS
        result.output_key = outcome.output_key
        result.model = outcome.model
        if not owns or locked is None:
            logger.warning(
                "tab %s %s attempt %s: 결과만 기록 (최신 시도 아님)", tab_id, stage, attempt
            )
            db.commit()
            return False
        if stage == LAST_STAGE:
            reasons = list(dict.fromkeys([*locked.review_reasons, *outcome.review_reasons]))
            locked.review_reasons = reasons
            locked.status = TabStatus.NEEDS_REVIEW if reasons else TabStatus.SUCCESS
        else:
            # 다음 단계를 큐에서 기다리는 동안은 queued (대기 시간을 실행 시간으로 세지 않는다)
            locked.status = TabStatus.QUEUED
        db.commit()
        return True


# ---- 단계별 작업 ----


def _stem(db: Session, tab: Tab) -> Stem:
    """이 타브를 만든 기타 음원. 없거나 파일이 없으면 STEM_MISSING (재시도 불가)."""
    stem = db.get(Stem, tab.stem_id)
    if stem is None or stem.deleted_at is not None or stem.storage_key is None:
        raise StageError(ErrorCode.STEM_MISSING)
    return stem


def _download(key: str, path: Path) -> None:
    try:
        storage.download_file(key, str(path))
    except ClientError as e:
        raise StageError(ErrorCode.STEM_MISSING, str(e)) from e


def transcription_work(db: Session, tab: Tab, tmp: Path) -> StageOutcome:
    """기타 음원에서 음을 찾는다. 음이 하나도 없으면 TAB_NO_NOTES."""
    from app.domains.tab.pipeline.transcription import transcribe

    stem = _stem(db, tab)
    guitar = tmp / "guitar.wav"
    _download(stem.storage_key or "", guitar)
    condition = conditions.playing_condition(tab.tuning_id, tab.capo)
    notes = transcribe(str(guitar), condition, settings)
    if not notes:
        raise StageError(ErrorCode.TAB_NO_NOTES)

    key = keys.notes_key(tab)
    storage.put_bytes(key, NOTES.dump_json(notes), JSON)
    return StageOutcome(output_key=key, model=f"basic-pitch {settings.ai_transcription_weights}")


def rhythm_work(db: Session, tab: Tab, tmp: Path) -> StageOutcome:
    """분리 전 구간 음원으로 박을 찾고 노트를 박 격자에 맞춘다.

    분리 음원 업로드는 드럼이 없는 기타 음원으로 추정하므로 TEMPO_UNCERTAIN.
    """
    from app.domains.tab.pipeline.rhythm import RhythmError, estimate_beats, quantize

    stem = _stem(db, tab)
    notes = NOTES.validate_json(storage.read_bytes(keys.notes_key(tab)))
    audio = tmp / "beat.wav"
    if stem.bypass_separation:
        _download(stem.storage_key or "", audio)
        offset_ms = 0
    else:
        _download(processing_keys.input_key(stem), audio)
        offset_ms = stem.input_pad_ms or 0
    grid = estimate_beats(str(audio), offset_ms, stem.end_ms - stem.start_ms, settings)
    try:
        result = quantize(notes, grid)
    except RhythmError as e:
        raise StageError(ErrorCode.RHYTHM_FAILED, str(e)) from e

    output = RhythmOutput(
        notes=result.notes,
        tempo_bpm=result.tempo_bpm,
        beats_per_bar=result.beats_per_bar,
        non_4_4=result.non_4_4,
        tempo_uncertain=stem.bypass_separation,
    )
    key = keys.rhythm_key(tab)
    storage.put_bytes(key, output.model_dump_json().encode(), JSON)
    return StageOutcome(output_key=key, model=f"beat-this {settings.ai_beat_model}")


def review_reasons(output: TabOutput, rhythm: RhythmOutput) -> list[str]:
    """결과를 확인 필요로 둘 이유 (needs_review). 실패가 아니다."""
    reasons: list[str] = []
    notes = output.notes
    if (
        notes
        and sum(n.low_confidence for n in notes) / len(notes) >= settings.ai_low_confidence_ratio
    ):
        reasons.append(ReviewReason.LOW_CONFIDENCE_NOTES)
    if any(not n.playable for n in notes):
        reasons.append(ReviewReason.UNPLAYABLE_POSITIONS)
    if rhythm.tempo_uncertain:
        reasons.append(ReviewReason.TEMPO_UNCERTAIN)
    if rhythm.non_4_4:
        reasons.append(ReviewReason.NON_4_4_METER)
    return [str(r) for r in reasons]


def tab_work(db: Session, tab: Tab, tmp: Path) -> StageOutcome:
    """줄·프렛을 고르고 연주 가능성을 검사한 뒤 tab.json·MusicXML을 만든다."""
    from app.domains.tab.pipeline.fingering import assign_fingering
    from app.domains.tab.pipeline.musicxml import build_musicxml
    from app.domains.tab.pipeline.playability import check_playable

    rhythm = RhythmOutput.model_validate_json(storage.read_bytes(keys.rhythm_key(tab)))
    condition = conditions.playing_condition(tab.tuning_id, tab.capo)
    try:
        notes = check_playable(assign_fingering(rhythm.notes, condition))
        output = TabOutput(
            notes=notes, tempo_bpm=rhythm.tempo_bpm, tempo_uncertain=rhythm.tempo_uncertain
        )
        musicxml = build_musicxml(notes, condition, rhythm.tempo_bpm)
    except Exception as e:
        logger.exception("tab %s 타브 생성 실패", tab.id)
        raise StageError(ErrorCode.TAB_BUILD_FAILED, str(e)) from e

    storage.put_bytes(keys.musicxml_key(tab), musicxml, MUSICXML)
    key = keys.tab_json_key(tab)
    storage.put_bytes(key, output.model_dump_json().encode(), JSON)
    return StageOutcome(
        output_key=key, model="viterbi", review_reasons=review_reasons(output, rhythm)
    )


# ---- Celery 작업 ----

_WORK: dict[TabStage, StageWork] = {
    TabStage.TRANSCRIPTION: transcription_work,
    TabStage.RHYTHM: rhythm_work,
    TabStage.TAB: tab_work,
}


def _run(tab_id: int, stage: TabStage) -> int:
    if not run_stage(tab_id, stage, _WORK[stage]):
        raise Ignore()  # 실패·완료면 다음 단계(chain)를 실행하지 않는다
    return tab_id


@celery_app.task(
    name="tab.transcribe",
    queue="cpu",
    soft_time_limit=soft_limit_sec(TabStage.TRANSCRIPTION, settings),
    time_limit=hard_limit_sec(TabStage.TRANSCRIPTION, settings),
)
def transcribe(tab_id: int) -> int:
    """음 인식."""
    return _run(tab_id, TabStage.TRANSCRIPTION)


@celery_app.task(
    name="tab.quantize",
    queue="cpu",
    soft_time_limit=soft_limit_sec(TabStage.RHYTHM, settings),
    time_limit=hard_limit_sec(TabStage.RHYTHM, settings),
)
def quantize_rhythm(tab_id: int) -> int:
    """박 추정·리듬 정량화."""
    return _run(tab_id, TabStage.RHYTHM)


@celery_app.task(
    name="tab.build_tab",
    queue="cpu",
    soft_time_limit=soft_limit_sec(TabStage.TAB, settings),
    time_limit=hard_limit_sec(TabStage.TAB, settings),
)
def build_tab(tab_id: int) -> int:
    """운지·연주 가능성 검사·MusicXML."""
    return _run(tab_id, TabStage.TAB)


_TASKS = {
    TabStage.TRANSCRIPTION: transcribe,
    TabStage.RHYTHM: quantize_rhythm,
    TabStage.TAB: build_tab,
}


def enqueue_pipeline(tab_id: int, start: TabStage) -> None:
    """start 단계부터 끝까지 chain으로 등록한다. 앞 단계 결과는 저장소에 남아 있다."""
    steps = [_TASKS[s].si(tab_id) for s in STAGE_ORDER[STAGE_ORDER.index(start) :]]
    chain(*steps).apply_async()
