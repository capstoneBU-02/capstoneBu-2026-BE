import threading
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.processing import service
from app.domains.processing.models import Stem, StemStage, StemStageResult, StemStatus
from tests.domains.processing.conftest import EnqueueSpy, FakeSegments

WS = 10


def _set_status(db: Session, stem: Stem, status: StemStatus, **kw: object) -> None:
    stem.status = status
    for k, v in kw.items():
        setattr(stem, k, v)
    db.commit()


# ---- DB 없이 ----


def test_input_hash_changes_with_range_and_config(segments: FakeSegments) -> None:
    s = get_settings()
    a = segments.add(start_ms=1000, end_ms=5000)
    base = service.compute_input_hash(a, s)
    assert base == service.compute_input_hash(a, s)
    assert base != service.compute_input_hash(segments.add(start_ms=1500, end_ms=5000), s)
    assert base != service.compute_input_hash(segments.add(is_separated_stem=True), s)
    assert base != service.compute_input_hash(a, s.model_copy(update={"ai_config_version": "x"}))


# ---- DB ----


def test_request_creates_stem_and_enqueues_after_commit(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    result = service.request_stem(db, WS, 1, enqueue=enqueue)

    assert not result.is_reused
    stem = result.stem
    assert (stem.version, stem.status, stem.start_ms, stem.end_ms) == (1, "queued", 1000, 5000)
    assert enqueue.calls == [(stem.id, StemStage.PREPROCESS)]


def test_same_input_in_progress_is_409(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    service.request_stem(db, WS, 1, enqueue=enqueue)

    with pytest.raises(BusinessException) as e:
        service.request_stem(db, WS, 1, enqueue=enqueue)
    assert e.value.error_code is ErrorCode.STEM_IN_PROGRESS
    assert len(enqueue.calls) == 1


def test_completed_same_input_is_reused(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    first = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    _set_status(db, first, StemStatus.NEEDS_REVIEW)

    again = service.request_stem(db, WS, 1, enqueue=enqueue)
    assert again.is_reused and again.stem.id == first.id
    assert len(enqueue.calls) == 1


def test_failed_input_creates_new_version(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    first = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    _set_status(db, first, StemStatus.FAILED, error_code="AI_WORKER_ERROR")

    second = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    assert second.id != first.id and second.version == 2


def test_other_workspace_segment_is_404(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1, workspace_id=99)
    with pytest.raises(BusinessException) as e:
        service.request_stem(db, WS, 1, enqueue=enqueue)
    assert e.value.error_code is ErrorCode.SEGMENT_NOT_FOUND


def test_current_stem_follows_segment_range(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1, start_ms=1000, end_ms=5000)
    old = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    _set_status(db, old, StemStatus.SUCCESS)

    segments.add(segment_id=1, start_ms=2000, end_ms=6000)  # 구간 수정
    new = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    assert new.version == 2

    assert service.get_current_stem(db, 1, 2000, 6000).id == new.id  # type: ignore[union-attr]
    assert service.get_current_stem(db, 1, 1000, 5000).id == old.id  # type: ignore[union-attr]
    assert service.get_current_stem(db, 1, 0, 1000) is None


def test_get_stem_hides_other_workspace(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    stem = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    with pytest.raises(BusinessException) as e:
        service.get_owned_stem(db, 99, stem.id)
    assert e.value.error_code is ErrorCode.STEM_NOT_FOUND


def test_response_shows_failure_and_stages(
    db: Session, segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    stem = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    now = datetime.now(UTC)
    db.add_all(
        [
            StemStageResult(
                stem_id=stem.id, stage="preprocess", status="success", attempt=1, started_at=now
            ),
            StemStageResult(
                stem_id=stem.id, stage="separation", status="failed", attempt=1, started_at=now
            ),
        ]
    )
    _set_status(
        db, stem, StemStatus.FAILED, current_stage="separation", error_code="AI_WORKER_TIMEOUT"
    )

    res = service.to_stem_response(db, stem)
    assert [(s.stage, s.status) for s in res.stages] == [
        ("preprocess", "success"),
        ("separation", "failed"),
    ]
    assert res.failure is not None
    assert res.failure.retryable and res.failure.retries_left == 3
    assert res.failure.message == ErrorCode.AI_WORKER_TIMEOUT.message
    assert not res.playable


def test_retry_from_failed_stage(db: Session, segments: FakeSegments, enqueue: EnqueueSpy) -> None:
    segments.add(segment_id=1)
    stem = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    _set_status(
        db, stem, StemStatus.FAILED, current_stage="separation", error_code="AI_WORKER_TIMEOUT"
    )

    res = service.retry_stem(db, WS, stem.id, enqueue=enqueue)
    assert (res.retry_from, res.retry_count, res.retries_left, res.status) == (
        "separation",
        1,
        2,
        "queued",
    )
    assert enqueue.calls[-1] == (stem.id, StemStage.SEPARATION)


@pytest.mark.parametrize(
    ("status", "code", "retry_count", "expected"),
    [
        (StemStatus.SUCCESS, None, 0, ErrorCode.STEM_NOT_RETRYABLE),
        (StemStatus.FAILED, "SEPARATION_NO_GUITAR", 0, ErrorCode.STEM_NOT_RETRYABLE),
        (StemStatus.FAILED, "AI_WORKER_ERROR", 3, ErrorCode.RETRY_LIMIT_EXCEEDED),
    ],
)
def test_retry_rejected(
    db: Session,
    segments: FakeSegments,
    enqueue: EnqueueSpy,
    status: StemStatus,
    code: str | None,
    retry_count: int,
    expected: ErrorCode,
) -> None:
    segments.add(segment_id=1)
    stem = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    _set_status(db, stem, status, error_code=code, retry_count=retry_count)

    with pytest.raises(BusinessException) as e:
        service.retry_stem(db, WS, stem.id, enqueue=enqueue)
    assert e.value.error_code is expected


def test_cancel_only_queued(db: Session, segments: FakeSegments, enqueue: EnqueueSpy) -> None:
    segments.add(segment_id=1)
    stem = service.request_stem(db, WS, 1, enqueue=enqueue).stem
    assert service.cancel_stem(db, WS, stem.id).status == "canceled"

    with pytest.raises(BusinessException) as e:
        service.cancel_stem(db, WS, stem.id)
    assert e.value.error_code is ErrorCode.STEM_NOT_CANCELABLE


def test_concurrent_requests_create_one_stem(
    session_factory: Callable[[], Session], segments: FakeSegments, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def call() -> None:
        with session_factory() as s:
            barrier.wait()
            try:
                service.request_stem(s, WS, 1, enqueue=enqueue)
                outcomes.append("created")
            except BusinessException as e:
                outcomes.append(e.error_code.code)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["STEM_IN_PROGRESS", "created"]
    assert len(enqueue.calls) == 1
