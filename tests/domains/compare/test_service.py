import json
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.compare import service
from app.domains.compare.models import (
    Comparison,
    ComparisonRegion,
    ComparisonStage,
    ComparisonStatus,
)
from app.domains.processing.models import Stem, StemStatus
from tests.domains.compare.conftest import WS, FakePerformances, FakeStorage
from tests.domains.conftest import EnqueueSpy, FakeSegments


def _set(db: Session, c: Comparison, status: ComparisonStatus, **kw: object) -> None:
    c.status = status
    for k, v in kw.items():
        setattr(c, k, v)
    db.commit()


def _request(db: Session, enqueue: EnqueueSpy, performance_id: int = 500) -> Comparison:
    return service.request_comparison(db, WS, performance_id, enqueue=enqueue).comparison


# ---- 비교 요청 ----


def test_request_creates_comparison_on_current_stem(
    db: Session, ready: Stem, enqueue: EnqueueSpy
) -> None:
    c = _request(db, enqueue)
    assert (c.status, c.stem_id, c.start_ms, c.end_ms, c.tab_id) == (
        "queued",
        ready.id,
        1000,
        5000,
        None,
    )
    assert enqueue.calls == [(c.id, ComparisonStage.ALIGN)]


def test_range_mismatch_is_409(
    db: Session, ready: Stem, performances: FakePerformances, enqueue: EnqueueSpy
) -> None:
    performances.add(501, start_ms=0, end_ms=5000)  # 구간 수정 전에 녹음
    with pytest.raises(BusinessException) as e:
        _request(db, enqueue, 501)
    assert e.value.error_code is ErrorCode.SEGMENT_RANGE_MISMATCH


@pytest.mark.parametrize("status", [StemStatus.PROCESSING, StemStatus.FAILED])
def test_stem_not_ready_is_409(
    db: Session,
    segments: FakeSegments,
    performances: FakePerformances,
    make_stem: Callable[..., Stem],
    enqueue: EnqueueSpy,
    status: StemStatus,
) -> None:
    segments.add(segment_id=1)
    performances.add(500)
    make_stem(status=status)
    with pytest.raises(BusinessException) as e:
        _request(db, enqueue)
    assert e.value.error_code is ErrorCode.STEM_NOT_READY


def test_no_stem_is_409(
    db: Session, segments: FakeSegments, performances: FakePerformances, enqueue: EnqueueSpy
) -> None:
    segments.add(segment_id=1)
    performances.add(500)
    with pytest.raises(BusinessException) as e:
        _request(db, enqueue)
    assert e.value.error_code is ErrorCode.STEM_NOT_READY


def test_uses_stem_of_current_range(
    db: Session,
    segments: FakeSegments,
    performances: FakePerformances,
    make_stem: Callable[..., Stem],
    enqueue: EnqueueSpy,
) -> None:
    make_stem(start_ms=0, end_ms=4000, version=1)  # 이전 범위
    current = make_stem(start_ms=2000, end_ms=6000, version=2)
    segments.add(segment_id=1, start_ms=2000, end_ms=6000)
    performances.add(500, start_ms=2000, end_ms=6000)
    assert _request(db, enqueue).stem_id == current.id


def test_other_workspace_performance_is_404(
    db: Session, ready: Stem, performances: FakePerformances, enqueue: EnqueueSpy
) -> None:
    performances.add(502, workspace_id=99)
    with pytest.raises(BusinessException) as e:
        _request(db, enqueue, 502)
    assert e.value.error_code is ErrorCode.PERFORMANCE_NOT_FOUND


def test_in_progress_409_done_reused_failed_new(
    db: Session, ready: Stem, enqueue: EnqueueSpy
) -> None:
    first = _request(db, enqueue)
    with pytest.raises(BusinessException) as e:
        _request(db, enqueue)
    assert e.value.error_code is ErrorCode.COMPARISON_IN_PROGRESS

    _set(db, first, ComparisonStatus.NOT_COMPARABLE, not_comparable_reason="LOW_MATCH_RATE")
    again = service.request_comparison(db, WS, 500, enqueue=enqueue)
    assert again.is_reused and again.comparison.id == first.id

    _set(db, first, ComparisonStatus.FAILED, error_code="AI_WORKER_ERROR")
    assert _request(db, enqueue).id != first.id


def test_publish_failure_marks_failed(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    enqueue.fail = True
    c = _request(db, enqueue)
    res = service.to_status_response(db, service.get_owned_comparison(db, WS, c.id))
    assert res.failure is not None
    assert (res.status, res.failure.error_code, res.failure.retryable) == (
        "failed",
        "AI_WORKER_ERROR",
        True,
    )


def test_stale_processing_expires(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    c = _request(db, enqueue)
    _set(db, c, ComparisonStatus.PROCESSING, current_stage="transcription")
    db.execute(
        text("UPDATE comparisons SET updated_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": c.id},
    )
    db.commit()
    db.expire_all()
    got = service.get_owned_comparison(db, WS, c.id)
    assert (got.status, got.error_code) == ("failed", "AI_WORKER_TIMEOUT")


# ---- 결과·재생·확인 표시 ----


def _done(db: Session, c: Comparison, fake_storage: FakeStorage) -> list[ComparisonRegion]:
    key = f"{c.id}/result.json"
    fake_storage.files[key] = json.dumps(
        {"overall_tempo": {"is_slow": True, "slope": 1.2}}
    ).encode()
    regions = [
        ComparisonRegion(
            comparison_id=c.id,
            type="pitch_difference",
            start_ms=3200,
            end_ms=4100,
            performance_start_ms=3800,
            performance_end_ms=4800,
        ),
        ComparisonRegion(
            comparison_id=c.id,
            type="unavailable",
            start_ms=100,
            end_ms=900,
            performance_start_ms=150,
            performance_end_ms=1000,
            reason="LOW_CONFIDENCE",
        ),
    ]
    db.add_all(regions)
    _set(db, c, ComparisonStatus.SUCCESS, result_key=key)
    return regions


def test_result_not_ready_is_409(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    c = _request(db, enqueue)
    with pytest.raises(BusinessException) as e:
        service.get_result(db, WS, c.id)
    assert e.value.error_code is ErrorCode.COMPARISON_NOT_READY


def test_result_contains_baseline_summary_regions(
    db: Session, ready: Stem, enqueue: EnqueueSpy, fake_storage: FakeStorage
) -> None:
    c = _request(db, enqueue)
    _done(db, c, fake_storage)

    res = service.get_result(db, WS, c.id)
    assert res.is_draft and res.status == "success"
    assert res.baseline.source == "original_song"
    assert (res.baseline.stem_id, res.baseline.segment.name) == (ready.id, "VERSE")
    assert res.overall_tempo is not None and res.overall_tempo.is_slow
    assert (res.summary.difference_count, res.summary.unavailable_count) == (1, 1)
    assert [r.start_ms for r in res.regions] == [100, 3200]  # 시간 순


def test_playback_is_partial_without_original(
    db: Session, ready: Stem, enqueue: EnqueueSpy, fake_storage: FakeStorage
) -> None:
    c = _request(db, enqueue)
    _done(db, c, fake_storage)

    res = service.get_playback(db, WS, c.id)
    by_type = {t.type: t for t in res.tracks}
    assert res.is_partial  # 원곡 경로는 IN 연결 전이라 없음
    assert not by_type["original"].available and by_type["original"].offset_ms == 1000
    assert by_type["stem"].url and by_type["performance"].url


def test_check_region(
    db: Session, ready: Stem, enqueue: EnqueueSpy, fake_storage: FakeStorage
) -> None:
    c = _request(db, enqueue)
    region = _done(db, c, fake_storage)[0]
    assert service.check_region(db, WS, c.id, region.id, True).is_checked

    with pytest.raises(BusinessException) as e:
        service.check_region(db, WS, c.id, 9999, True)
    assert e.value.error_code is ErrorCode.REGION_NOT_FOUND


# ---- 재시도 ----


def test_retry_from_failed_stage(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    c = _request(db, enqueue)
    _set(
        db,
        c,
        ComparisonStatus.FAILED,
        current_stage="transcription",
        error_code="AI_WORKER_TIMEOUT",
    )
    res = service.retry_comparison(db, WS, c.id, enqueue=enqueue)
    assert (res.retry_from, res.retries_left) == ("transcription", 2)
    assert enqueue.calls[-1] == (c.id, ComparisonStage.TRANSCRIPTION)


@pytest.mark.parametrize(
    ("status", "code", "count", "expected"),
    [
        (ComparisonStatus.NOT_COMPARABLE, None, 0, ErrorCode.COMPARISON_NOT_RETRYABLE),
        (
            ComparisonStatus.FAILED,
            "PERFORMANCE_INVALID_AUDIO",
            0,
            ErrorCode.COMPARISON_NOT_RETRYABLE,
        ),
        (ComparisonStatus.FAILED, "AI_WORKER_ERROR", 3, ErrorCode.RETRY_LIMIT_EXCEEDED),
    ],
)
def test_retry_rejected(
    db: Session,
    ready: Stem,
    enqueue: EnqueueSpy,
    status: ComparisonStatus,
    code: str | None,
    count: int,
    expected: ErrorCode,
) -> None:
    c = _request(db, enqueue)
    _set(db, c, status, error_code=code, retry_count=count)
    with pytest.raises(BusinessException) as e:
        service.retry_comparison(db, WS, c.id, enqueue=enqueue)
    assert e.value.error_code is expected


def test_queued_stale_threshold_uses_setting(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    c = _request(db, enqueue)
    assert not service.is_stale(c)
    db.execute(
        text(
            "UPDATE comparisons SET updated_at = now() - make_interval(secs => :s) WHERE id = :id"
        ),
        {"s": get_settings().ai_job_queued_stale_sec + 5, "id": c.id},
    )
    db.commit()
    db.expire_all()
    assert service.get_owned_comparison(db, WS, c.id).status == "failed"
