"""연주 비교 Celery 작업. align → transcribe → compare (모두 cpu 큐).

작업은 comparison_id만 받고, 단계 결과는 저장소 JSON(contract 형태)으로 주고받는다.
판정 함수(pipeline/judge.py)는 복재환이 만들고, 여기서는 입출력과 상태 기록만 맡는다.
"""

import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from botocore.exceptions import ClientError
from celery import chain
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import ErrorCode
from app.domains.compare import keys, sources
from app.domains.compare.limits import hard_limit_sec, soft_limit_sec
from app.domains.compare.models import (
    DONE_STATUSES,
    STAGE_ORDER,
    Comparison,
    ComparisonRegion,
    ComparisonStage,
    ComparisonStageResult,
    ComparisonStatus,
    StageStatus,
)
from app.domains.compare.pipeline import contract
from app.domains.processing.models import Stem

logger = logging.getLogger(__name__)
settings = get_settings()

JSON = "application/json"
LAST_STAGE = ComparisonStage.COMPARE


class StageError(Exception):
    """예상된 실패. error_code로 사용자에게 안내한다."""

    def __init__(self, error_code: ErrorCode, detail: str | None = None) -> None:
        """error_code와 상세 설명(로그용)을 받는다."""
        self.error_code = error_code
        super().__init__(detail or error_code.message)


@dataclass(frozen=True)
class StageOutcome:
    """단계 실행 결과. run_stage가 비교와 단계 결과에 반영한다."""

    output_key: str | None
    model: str | None = None
    # 채워지면 이 단계에서 not_comparable로 끝낸다 (다시 녹음 안내 등)
    not_comparable_reason: str | None = None
    result_key: str | None = None  # result.json을 쓴 경우
    regions: list[contract.Region] = field(default_factory=list)  # compare 단계만


StageWork = Callable[[Session, Comparison, Path], StageOutcome]


def _now() -> datetime:
    """UTC 현재 시각."""
    return datetime.now(UTC)


def _lock(db: Session, comparison_id: int) -> Comparison | None:
    """비교 행을 잠그고 DB의 최신 값으로 읽는다."""
    return db.scalars(
        select(Comparison)
        .where(Comparison.id == comparison_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _latest_attempt(
    db: Session, comparison_id: int, stage: ComparisonStage
) -> ComparisonStageResult | None:
    """같은 단계의 마지막 시도."""
    return db.scalars(
        select(ComparisonStageResult)
        .where(
            ComparisonStageResult.comparison_id == comparison_id,
            ComparisonStageResult.stage == stage,
        )
        .order_by(ComparisonStageResult.attempt.desc())
        .limit(1)
    ).first()


def _skip_reason(db: Session, c: Comparison, stage: ComparisonStage) -> str | None:
    """실행하지 않을 이유. None이면 실행한다 (JOB의 run_stage와 같은 규칙)."""
    if c.deleted_at is not None:
        return "deleted"
    if c.status in (ComparisonStatus.FAILED, *DONE_STATUSES):
        return f"status={c.status}"
    last = _latest_attempt(db, c.id, stage)
    if last is None:
        return None
    if last.status == StageStatus.SUCCESS:
        return "stage done"
    if last.status == StageStatus.PROCESSING and last.started_at is not None:
        deadline = last.started_at + timedelta(seconds=hard_limit_sec(stage, settings))
        if _now() < deadline:
            return "stage running elsewhere"
    return None


def _finish_not_comparable(db: Session, c: Comparison, stage: ComparisonStage, reason: str) -> None:
    """not_comparable로 끝내고 남은 단계는 skipped로 남긴다."""
    c.status = ComparisonStatus.NOT_COMPARABLE
    c.not_comparable_reason = reason
    for later in STAGE_ORDER[STAGE_ORDER.index(stage) + 1 :]:
        db.add(
            ComparisonStageResult(
                comparison_id=c.id,
                stage=later,
                status=StageStatus.SKIPPED,
                attempt=1,
                started_at=_now(),
                finished_at=_now(),
            )
        )


def _save_regions(db: Session, c: Comparison, regions: list[contract.Region]) -> None:
    """구간을 새로 저장한다. 같은 비교를 다시 실행해도 중복되지 않게 먼저 지운다."""
    db.flush()  # 아직 쓰지 않은 행도 DELETE 대상이 되도록 먼저 반영한다
    db.execute(delete(ComparisonRegion).where(ComparisonRegion.comparison_id == c.id))
    for r in regions:
        db.add(
            ComparisonRegion(
                comparison_id=c.id,
                type=r.type,
                start_ms=r.start_ms,
                end_ms=r.end_ms,
                performance_start_ms=r.performance_start_ms,
                performance_end_ms=r.performance_end_ms,
                reason=r.reason,
                is_checked=False,
            )
        )


def run_stage(
    comparison_id: int,
    stage: ComparisonStage,
    work: StageWork,
    session_factory: Callable[[], Session] = SessionLocal,
) -> bool:
    """한 단계를 실행하고 결과를 기록한다. 다음 단계로 진행할 수 있으면 True."""
    with session_factory() as db:
        c = _lock(db, comparison_id)
        if c is None:
            logger.warning("comparison %s 없음 — 건너뜀", comparison_id)
            return False
        reason = _skip_reason(db, c, stage)
        if reason is not None:
            logger.info("comparison %s %s 건너뜀 (%s)", comparison_id, stage, reason)
            return reason == "stage done"

        last = _latest_attempt(db, c.id, stage)
        attempt = (last.attempt if last else 0) + 1
        result = ComparisonStageResult(
            comparison_id=c.id,
            stage=stage,
            status=StageStatus.PROCESSING,
            attempt=attempt,
            started_at=_now(),
        )
        db.add(result)
        c.status = ComparisonStatus.PROCESSING
        c.current_stage = stage
        db.commit()

        error: ErrorCode | None = None
        outcome: StageOutcome | None = None
        try:
            with tempfile.TemporaryDirectory(prefix=f"cmp{comparison_id}-") as tmp:
                outcome = work(db, c, Path(tmp))
        except StageError as e:
            error = e.error_code
        except contract.InvalidAudioError:
            error = ErrorCode.PERFORMANCE_INVALID_AUDIO
        except SoftTimeLimitExceeded:
            error = ErrorCode.AI_WORKER_TIMEOUT
        except Exception:
            logger.exception("comparison %s %s 처리 중 오류", comparison_id, stage)
            error = ErrorCode.AI_WORKER_ERROR

        # 다시 잠근 뒤, 이 시도가 아직 최신일 때만 비교에 반영한다
        locked = _lock(db, comparison_id)
        newest = db.scalar(
            select(func.max(ComparisonStageResult.attempt)).where(
                ComparisonStageResult.comparison_id == comparison_id,
                ComparisonStageResult.stage == stage,
            )
        )
        owns = (
            locked is not None
            and newest == attempt
            and locked.status == ComparisonStatus.PROCESSING
            and locked.current_stage == stage
        )
        result.finished_at = _now()

        if error is not None or outcome is None:
            code = (error or ErrorCode.AI_WORKER_ERROR).code
            result.status = StageStatus.FAILED
            result.error_code = code
            if owns and locked is not None:
                locked.status = ComparisonStatus.FAILED
                locked.error_code = code
            db.commit()
            return False

        result.status = StageStatus.SUCCESS
        result.output_key = outcome.output_key
        result.model = outcome.model
        if not owns or locked is None:
            logger.warning(
                "comparison %s %s attempt %s: 결과만 기록 (최신 시도 아님)",
                comparison_id,
                stage,
                attempt,
            )
            db.commit()
            return False
        if outcome.result_key:
            locked.result_key = outcome.result_key
        if outcome.not_comparable_reason:
            _finish_not_comparable(db, locked, stage, outcome.not_comparable_reason)
            db.commit()
            return False  # 남은 단계는 실행하지 않는다
        if stage == LAST_STAGE:
            _save_regions(db, locked, outcome.regions)
            locked.status = ComparisonStatus.SUCCESS
        db.commit()
        return True


# ---- 단계별 작업 ----


def _download_inputs(db: Session, c: Comparison, tmp: Path) -> tuple[str, str]:
    """기준 기타 음원과 내 연주를 내려받는다. 기준 음원이 없으면 STEM_MISSING."""
    stem = db.get(Stem, c.stem_id)
    if stem is None or stem.storage_key is None:
        raise StageError(ErrorCode.STEM_MISSING)
    ref = tmp / "reference.wav"
    perf = tmp / "performance"
    try:
        storage.download_file(stem.storage_key, str(ref))
    except ClientError as e:
        raise StageError(ErrorCode.STEM_MISSING, str(e)) from e
    perf_src = sources.load_performance(db, c.workspace_id, c.performance_id)
    storage.download_file(perf_src.storage_key, str(perf))
    return str(ref), str(perf)


def _write_result(c: Comparison, alignment: contract.AlignmentResult | None) -> str:
    """result.json: 전체 빠르기. 구간 목록은 comparison_regions에 따로 저장한다."""
    overall = {"is_slow": alignment.is_slow, "slope": alignment.slope} if alignment else None
    key = keys.result_key(c)
    storage.put_bytes(key, json.dumps({"overall_tempo": overall}).encode(), JSON)
    return key


def align_work(db: Session, c: Comparison, tmp: Path) -> StageOutcome:
    """처리 전 입력 검사 + DTW 정렬. 다시 녹음이 필요하면 여기서 끝낸다."""
    from app.domains.compare.pipeline import judge

    ref, perf = _download_inputs(db, c, tmp)
    res = judge.align(ref, perf, settings)
    key = keys.alignment_key(c)
    storage.put_bytes(key, contract.to_json(res), JSON)
    if res.rerecord_reason:
        return StageOutcome(
            output_key=key,
            not_comparable_reason=res.rerecord_reason,
            result_key=_write_result(c, res),
        )
    return StageOutcome(output_key=key)


def transcription_work(db: Session, c: Comparison, tmp: Path) -> StageOutcome:
    """양쪽 연주 음 인식. 인식 음이 너무 적으면 다시 녹음 안내로 끝낸다."""
    from app.domains.compare.pipeline import judge

    ref, perf = _download_inputs(db, c, tmp)
    res = judge.transcribe_both(ref, perf, settings)
    key = keys.notes_key(c)
    storage.put_bytes(key, contract.to_json(res), JSON)
    if res.rerecord_reason:
        alignment = contract.alignment_from_json(storage.read_bytes(keys.alignment_key(c)))
        return StageOutcome(
            output_key=key,
            model=settings.ai_transcription_weights,
            not_comparable_reason=res.rerecord_reason,
            result_key=_write_result(c, alignment),
        )
    return StageOutcome(output_key=key, model=settings.ai_transcription_weights)


def compare_work(db: Session, c: Comparison, tmp: Path) -> StageOutcome:
    """정렬·음 인식 결과로 차이·판단 보류 구간을 판정한다."""
    from app.domains.compare.pipeline import judge

    alignment = contract.alignment_from_json(storage.read_bytes(keys.alignment_key(c)))
    notes = contract.notes_from_json(storage.read_bytes(keys.notes_key(c)))
    res = judge.judge(alignment, notes, settings)
    key = _write_result(c, alignment)
    return StageOutcome(
        output_key=key,
        not_comparable_reason=res.not_comparable_reason,
        result_key=key,
        regions=res.regions,
    )


# ---- Celery 작업 ----

_WORK: dict[ComparisonStage, StageWork] = {
    ComparisonStage.ALIGN: align_work,
    ComparisonStage.TRANSCRIPTION: transcription_work,
    ComparisonStage.COMPARE: compare_work,
}


def _run(comparison_id: int, stage: ComparisonStage) -> int:
    """단계를 실행하고, 진행할 수 없으면 chain을 멈춘다."""
    if not run_stage(comparison_id, stage, _WORK[stage]):
        raise Ignore()  # 실패·not_comparable이면 다음 단계(chain)를 실행하지 않는다
    return comparison_id


@celery_app.task(
    name="compare.align",
    queue="cpu",
    soft_time_limit=soft_limit_sec(ComparisonStage.ALIGN, settings),
    time_limit=hard_limit_sec(ComparisonStage.ALIGN, settings),
)
def align(comparison_id: int) -> int:
    """처리 전 입력 검사와 시간 정렬."""
    return _run(comparison_id, ComparisonStage.ALIGN)


@celery_app.task(
    name="compare.transcribe",
    queue="cpu",
    soft_time_limit=soft_limit_sec(ComparisonStage.TRANSCRIPTION, settings),
    time_limit=hard_limit_sec(ComparisonStage.TRANSCRIPTION, settings),
)
def transcribe(comparison_id: int) -> int:
    """양쪽 연주 음 인식."""
    return _run(comparison_id, ComparisonStage.TRANSCRIPTION)


@celery_app.task(
    name="compare.compare",
    queue="cpu",
    soft_time_limit=soft_limit_sec(ComparisonStage.COMPARE, settings),
    time_limit=hard_limit_sec(ComparisonStage.COMPARE, settings),
)
def compare(comparison_id: int) -> int:
    """차이·판단 보류 구간 판정."""
    return _run(comparison_id, ComparisonStage.COMPARE)


_TASKS = {
    ComparisonStage.ALIGN: align,
    ComparisonStage.TRANSCRIPTION: transcribe,
    ComparisonStage.COMPARE: compare,
}


def enqueue_pipeline(comparison_id: int, start: ComparisonStage) -> None:
    """start 단계부터 끝까지 chain으로 등록한다. 앞 단계 결과는 저장소에 남아 있다."""
    steps = [_TASKS[s].si(comparison_id) for s in STAGE_ORDER[STAGE_ORDER.index(start) :]]
    chain(*steps).apply_async()
