from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.processing.models import Stem, StemStatus
from app.domains.tab import service
from app.domains.tab.models import Tab, TabStage, TabStatus
from app.domains.tab.schemas import TabCreateRequest
from tests.domains.conftest import EnqueueSpy, FakeSegments

WS = 10
DEFAULT = TabCreateRequest()


def _code(exc: pytest.ExceptionInfo[BusinessException]) -> str:
    return exc.value.error_code.code


def _set(db: Session, tab: Tab, **kw: object) -> None:
    for k, v in kw.items():
        setattr(tab, k, v)
    db.commit()


def test_request_creates_queued_tab_and_enqueues(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    stem = ready_stem()

    result = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)

    tab = result.tab
    assert not result.is_reused
    assert (tab.status, tab.version, tab.stem_id) == (TabStatus.QUEUED, 1, stem.id)
    assert (tab.result_type, tab.tuning_id, tab.capo) == ("original", "e_standard", 0)
    assert tab.review_reasons == []
    assert enqueue.calls == [(tab.id, TabStage.TRANSCRIPTION)]


def test_same_condition_in_progress_is_409(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)

    with pytest.raises(BusinessException) as exc:
        service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)
    assert _code(exc) == "TAB_IN_PROGRESS"


def test_completed_same_condition_is_reused(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    first = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab
    _set(db, first, status=TabStatus.NEEDS_REVIEW)

    again = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)

    assert again.is_reused
    assert again.tab.id == first.id
    assert len(enqueue.calls) == 1


def test_failed_tab_makes_new_version(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    first = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab
    _set(db, first, status=TabStatus.FAILED, error_code="TAB_NO_NOTES")

    second = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab

    assert second.version == 2


def test_stem_needs_review_is_carried_into_review_reasons(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem(status=StemStatus.NEEDS_REVIEW)

    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab

    assert tab.review_reasons == ["STEM_NEEDS_REVIEW"]


@pytest.mark.parametrize("status", [StemStatus.QUEUED, StemStatus.FAILED])
def test_stem_not_ready_is_409(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy, status: StemStatus
) -> None:
    ready_stem(status=status)

    with pytest.raises(BusinessException) as exc:
        service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)
    assert _code(exc) == "STEM_NOT_READY"


def test_no_stem_is_409(db: Session, segments: FakeSegments, enqueue: EnqueueSpy) -> None:
    segments.add(segment_id=1)

    with pytest.raises(BusinessException) as exc:
        service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)
    assert _code(exc) == "STEM_NOT_READY"


@pytest.mark.parametrize(
    "req",
    [
        TabCreateRequest(result_type="arrangement"),
        TabCreateRequest(tuning_id="drop_d"),
        TabCreateRequest(capo=2),
    ],
)
def test_unsupported_condition_is_422(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy, req: TabCreateRequest
) -> None:
    ready_stem()

    with pytest.raises(BusinessException) as exc:
        service.request_tab(db, WS, 1, req, enqueue=enqueue)
    assert _code(exc) == "UNSUPPORTED_CONDITION"


def test_other_workspace_segment_is_404(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()

    with pytest.raises(BusinessException) as exc:
        service.request_tab(db, 99, 1, DEFAULT, enqueue=enqueue)
    assert _code(exc) == "SEGMENT_NOT_FOUND"


def test_enqueue_failure_leaves_retryable_failed_tab(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    enqueue.fail = True

    with pytest.raises(BusinessException) as exc:
        service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)

    assert _code(exc) == "JOB_ENQUEUE_FAILED"
    tab = db.query(Tab).one()
    assert (tab.status, tab.error_code) == (TabStatus.FAILED, "AI_WORKER_ERROR")


def test_list_is_latest_first(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    first = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab
    _set(db, first, status=TabStatus.FAILED, error_code="TAB_NO_NOTES")
    service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue)

    items = service.list_tabs(db, WS, 1).items

    assert [(i.version, i.is_latest) for i in items] == [(2, True), (1, False)]


def test_get_hides_other_workspace(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab

    with pytest.raises(BusinessException) as exc:
        service.get_owned_tab(db, 99, tab.id)
    assert _code(exc) == "TAB_NOT_FOUND"


def test_response_shows_failure_and_stages(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab
    _set(db, tab, status=TabStatus.FAILED, current_stage="rhythm", error_code="RHYTHM_FAILED")

    res = service.to_tab_response(db, tab)

    assert [s.stage for s in res.stages] == ["transcription", "rhythm", "tab"]
    assert res.failure is not None
    assert (res.failure.stage, res.failure.retryable, res.failure.retries_left) == (
        "rhythm",
        True,
        3,
    )
    assert res.failure.message == ErrorCode.RHYTHM_FAILED.message


def test_result_before_done_is_409(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab

    with pytest.raises(BusinessException) as exc:
        service.get_tab_result(db, WS, tab.id)
    assert _code(exc) == "TAB_NOT_READY"


def test_retry_from_failed_stage(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab
    _set(db, tab, status=TabStatus.FAILED, current_stage="rhythm", error_code="RHYTHM_FAILED")

    res = service.retry_tab(db, WS, tab.id, enqueue=enqueue)

    assert (res.retry_from, res.retry_count, res.retries_left) == ("rhythm", 1, 2)
    assert enqueue.calls[-1] == (tab.id, TabStage.RHYTHM)


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"status": TabStatus.QUEUED}, "TAB_NOT_RETRYABLE"),
        ({"status": TabStatus.FAILED, "error_code": "STEM_MISSING"}, "TAB_NOT_RETRYABLE"),
        (
            {"status": TabStatus.FAILED, "error_code": "AI_WORKER_ERROR", "retry_count": 3},
            "RETRY_LIMIT_EXCEEDED",
        ),
    ],
)
def test_retry_rejected(
    db: Session,
    ready_stem: Callable[..., Stem],
    enqueue: EnqueueSpy,
    fields: dict[str, object],
    code: str,
) -> None:
    ready_stem()
    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab
    _set(db, tab, **fields)

    with pytest.raises(BusinessException) as exc:
        service.retry_tab(db, WS, tab.id, enqueue=enqueue)
    assert _code(exc) == code


def test_stale_queued_tab_is_expired_on_read(
    db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy
) -> None:
    ready_stem()
    tab = service.request_tab(db, WS, 1, DEFAULT, enqueue=enqueue).tab

    assert service.is_stale(tab, now=datetime.now(UTC) + timedelta(hours=1))
    assert not service.is_stale(tab)


def test_download_rejects_unknown_format(db: Session) -> None:
    with pytest.raises(BusinessException) as exc:
        service.get_download(db, WS, 1, "pdf")
    assert _code(exc) == "UNSUPPORTED_DOWNLOAD_FORMAT"


def test_conditions_list_defaults() -> None:
    res = service.supported_conditions()

    assert [t.id for t in res.tunings] == ["e_standard"]
    assert res.tunings[0].notes == ["E2", "A2", "D3", "G3", "B3", "E4"]
    assert (res.capo.min, res.capo.max) == (0, 0)
    assert res.default.result_type == "original"
