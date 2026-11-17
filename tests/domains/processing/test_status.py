"""구간 처리 상태 통합 조회 (이슈 #48, JOB-03·JOB-04)."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.processing import sources, status
from app.domains.processing.models import Stem, StemStatus
from app.domains.tab.models import Tab, TabStatus
from app.main import create_app
from tests.domains.conftest import FakeSegments

WS = 10


def _stem(
    db: Session,
    version: int = 1,
    st: str = StemStatus.SUCCESS,
    start_ms: int = 1000,
    end_ms: int = 5000,
) -> Stem:
    stem = Stem(
        workspace_id=WS,
        track_id=100,
        segment_id=1,
        start_ms=start_ms,
        end_ms=end_ms,
        version=version,
        part="guitar",
        status=st,
        bypass_separation=False,
        storage_key=f"10/100/stems/1/v{version}/guitar.wav" if st == StemStatus.SUCCESS else None,
        input_hash=f"stemhash{version}",
        config_version="test",
        retry_count=0,
    )
    db.add(stem)
    db.commit()
    return stem


def _tab(db: Session, stem: Stem, version: int, st: str, retry_count: int = 0) -> Tab:
    tab = Tab(
        workspace_id=WS,
        track_id=100,
        segment_id=1,
        stem_id=stem.id,
        version=version,
        status=st,
        current_stage="transcription",
        retry_count=retry_count,
        input_hash=f"tab-{stem.id}-{version}",
        config_version="test",
    )
    db.add(tab)
    db.commit()
    return tab


def test_nothing_requested(db: Session, segments: FakeSegments) -> None:
    segments.add(segment_id=1)
    res = status.get_processing_status(db, WS, 1)
    assert (res.segment_id, res.stem, res.tab, res.updated_at) == (1, None, None, None)


def test_stem_success_tab_failed_shown_separately(db: Session, segments: FakeSegments) -> None:
    """분리 성공·타브 실패를 하나로 합치지 않는다 (JOB-04)."""
    segments.add(segment_id=1)
    stem = _stem(db)
    _tab(db, stem, 1, TabStatus.SUCCESS)
    failed = _tab(db, stem, 2, TabStatus.FAILED, retry_count=1)

    res = status.get_processing_status(db, WS, 1)
    assert res.stem is not None and res.tab is not None
    assert (res.stem.stem_id, res.stem.status, res.stem.playable) == (stem.id, "success", True)
    assert (res.tab.tab_id, res.tab.status, res.tab.retryable) == (failed.id, "failed", True)
    assert res.updated_at == max(stem.updated_at, failed.updated_at)


def test_tab_retry_limit(db: Session, segments: FakeSegments) -> None:
    segments.add(segment_id=1)
    stem = _stem(db)
    _tab(db, stem, 1, TabStatus.FAILED, retry_count=3)
    res = status.get_processing_status(db, WS, 1)
    assert res.tab is not None and res.tab.retryable is False


def test_stem_of_old_range_is_not_current(db: Session, segments: FakeSegments) -> None:
    """구간 범위가 바뀌면 이전 범위의 기타 음원·타브는 현재 상태가 아니다."""
    segments.add(segment_id=1, start_ms=1000, end_ms=5000)
    old = _stem(db)
    _tab(db, old, 1, TabStatus.SUCCESS)
    segments.add(segment_id=1, start_ms=2000, end_ms=6000)  # 구간 수정

    res = status.get_processing_status(db, WS, 1)
    assert (res.stem, res.tab) == (None, None)


def test_stale_stem_is_expired(db: Session, segments: FakeSegments) -> None:
    """멈춘 작업은 조회하면서 실패로 정리한다 (GET /stems/{id}와 같은 판단)."""
    segments.add(segment_id=1)
    stem = _stem(db, st=StemStatus.QUEUED)
    stem.updated_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()

    res = status.get_processing_status(db, WS, 1)
    assert res.stem is not None
    assert (res.stem.status, res.stem.playable) == ("failed", False)


def test_other_workspace_is_404(db: Session, segments: FakeSegments) -> None:
    segments.add(segment_id=8, track_id=200, workspace_id=99)
    with pytest.raises(BusinessException) as e:
        status.get_processing_status(db, WS, 8)
    assert e.value.error_code is ErrorCode.SEGMENT_NOT_FOUND


@pytest.fixture
def api(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
    app = create_app()

    def _db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[sources.get_current_workspace_id] = lambda: WS
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_route(api: TestClient, db: Session, segments: FakeSegments) -> None:
    segments.add(segment_id=1)
    _stem(db)
    body = api.get("/api/v1/segments/1/processing-status").json()
    assert body["success"] is True
    assert body["data"]["stem"]["playable"] is True and body["data"]["tab"] is None
    assert api.get("/api/v1/segments/999/processing-status").status_code == 404
