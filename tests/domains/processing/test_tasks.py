from collections.abc import Callable
from pathlib import Path

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ErrorCode
from app.domains.processing import service
from app.domains.processing.models import Stem, StemStage, StemStageResult, StemStatus
from app.domains.processing.tasks import StageError, StageOutcome, run_stage
from tests.domains.processing.conftest import EnqueueSpy, FakeSegments

WS = 10


@pytest.fixture
def stem(db: Session, segments: FakeSegments, enqueue: EnqueueSpy) -> Stem:
    segments.add(segment_id=1)
    return service.request_stem(db, WS, 1, enqueue=enqueue).stem


def _results(db: Session, stem_id: int) -> list[tuple[str, str, int, str | None]]:
    rows = db.scalars(
        select(StemStageResult)
        .where(StemStageResult.stem_id == stem_id)
        .order_by(StemStageResult.id)
    ).all()
    return [(r.stage, r.status, r.attempt, r.error_code) for r in rows]


def _reload(db: Session, stem: Stem) -> Stem:
    db.expire_all()
    s = db.get(Stem, stem.id)
    assert s is not None
    return s


def ok(key: str, review: str | None = None, skipped: bool = False) -> Callable[..., StageOutcome]:
    def work(db: Session, stem: Stem, tmp: Path) -> StageOutcome:
        assert tmp.is_dir()
        return StageOutcome(output_key=key, model="m", review_reason=review, skipped=skipped)

    return work


def fail(exc: BaseException) -> Callable[..., StageOutcome]:
    def work(db: Session, stem: Stem, tmp: Path) -> StageOutcome:
        raise exc

    return work


def test_full_pipeline_success(
    db: Session, stem: Stem, session_factory: Callable[[], Session]
) -> None:
    assert run_stage(stem.id, StemStage.PREPROCESS, ok("in.wav"), session_factory)
    assert _reload(db, stem).status == StemStatus.PROCESSING

    assert run_stage(stem.id, StemStage.SEPARATION, ok("guitar.wav"), session_factory)
    s = _reload(db, stem)
    assert (s.status, s.storage_key, s.current_stage) == ("success", "guitar.wav", "separation")
    assert _results(db, stem.id) == [
        ("preprocess", "success", 1, None),
        ("separation", "success", 1, None),
    ]


def test_bypass_marks_separation_skipped_and_needs_review(
    db: Session, stem: Stem, session_factory: Callable[[], Session]
) -> None:
    run_stage(stem.id, StemStage.PREPROCESS, ok("in.wav"), session_factory)
    run_stage(
        stem.id,
        StemStage.SEPARATION,
        ok("guitar.wav", review="SEPARATION_UNCLEAR", skipped=True),
        session_factory,
    )
    s = _reload(db, stem)
    assert (s.status, s.review_reason) == ("needs_review", "SEPARATION_UNCLEAR")
    assert _results(db, stem.id)[-1][:2] == ("separation", "skipped")


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (StageError(ErrorCode.PREPROCESS_INVALID_AUDIO), "PREPROCESS_INVALID_AUDIO"),
        (SoftTimeLimitExceeded(), "AI_WORKER_TIMEOUT"),
        (RuntimeError("boom"), "AI_WORKER_ERROR"),
    ],
)
def test_failure_records_error_code(
    db: Session,
    stem: Stem,
    session_factory: Callable[[], Session],
    exc: BaseException,
    code: str,
) -> None:
    assert not run_stage(stem.id, StemStage.PREPROCESS, fail(exc), session_factory)
    s = _reload(db, stem)
    assert (s.status, s.error_code, s.current_stage) == ("failed", code, "preprocess")
    assert _results(db, stem.id) == [("preprocess", "failed", 1, code)]


def test_retry_adds_new_attempt(
    db: Session, stem: Stem, session_factory: Callable[[], Session], enqueue: EnqueueSpy
) -> None:
    run_stage(stem.id, StemStage.PREPROCESS, ok("in.wav"), session_factory)
    run_stage(stem.id, StemStage.SEPARATION, fail(SoftTimeLimitExceeded()), session_factory)
    service.retry_stem(db, WS, stem.id, enqueue=enqueue)

    assert run_stage(stem.id, StemStage.SEPARATION, ok("guitar.wav"), session_factory)
    assert _results(db, stem.id)[-1] == ("separation", "success", 2, None)
    assert _reload(db, stem).status == "success"


def test_canceled_stem_is_not_processed(
    db: Session, stem: Stem, session_factory: Callable[[], Session]
) -> None:
    service.cancel_stem(db, WS, stem.id)
    called: list[int] = []

    def work(db: Session, s: Stem, tmp: Path) -> StageOutcome:
        called.append(s.id)
        return StageOutcome(output_key="x")

    assert not run_stage(stem.id, StemStage.PREPROCESS, work, session_factory)
    assert called == [] and _results(db, stem.id) == []


def test_redelivered_finished_stage_is_skipped(
    db: Session, stem: Stem, session_factory: Callable[[], Session]
) -> None:
    run_stage(stem.id, StemStage.PREPROCESS, ok("in.wav"), session_factory)
    # acks_late로 같은 작업이 다시 와도 다시 실행하지 않고 chain은 계속된다
    assert run_stage(stem.id, StemStage.PREPROCESS, fail(RuntimeError()), session_factory)
    assert _results(db, stem.id) == [("preprocess", "success", 1, None)]
