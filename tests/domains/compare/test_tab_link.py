"""연주 비교의 기준 타브: 비교에 쓴 기타 음원으로 만든 최신 완료 타브 (이슈 #46)."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domains.compare import service, sources
from app.domains.compare.models import ComparisonStatus
from app.domains.processing.models import Stem
from app.domains.tab.models import Tab, TabStatus
from tests.domains.compare.conftest import WS, FakeStorage
from tests.domains.conftest import EnqueueSpy


def _tab(db: Session, stem: Stem, version: int, status: str, *, deleted: bool = False) -> Tab:
    tab = Tab(
        workspace_id=stem.workspace_id,
        track_id=stem.track_id,
        segment_id=stem.segment_id,
        stem_id=stem.id,
        version=version,
        status=status,
        input_hash=f"tab-{stem.id}-{version}",
        config_version="test",
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(tab)
    db.commit()
    return tab


def test_no_tab_is_none(db: Session, ready: Stem) -> None:
    assert sources.find_latest_tab_id(db, ready.id) is None


def test_latest_done_tab_is_chosen(
    db: Session, ready: Stem, make_stem: Callable[..., Stem]
) -> None:
    # 높은 버전을 먼저 넣어 id 순서와 version 순서를 다르게 만든다
    review = _tab(db, ready, 2, TabStatus.NEEDS_REVIEW)
    done = _tab(db, ready, 1, TabStatus.SUCCESS)
    assert done.id > review.id
    assert sources.find_latest_tab_id(db, ready.id) == review.id  # id가 아니라 version 기준

    # 진행 중·실패·삭제된 더 새 버전은 건너뛴다
    _tab(db, ready, 3, TabStatus.QUEUED)
    _tab(db, ready, 4, TabStatus.FAILED)
    _tab(db, ready, 5, TabStatus.SUCCESS, deleted=True)
    assert sources.find_latest_tab_id(db, ready.id) == review.id

    # 다른 기타 음원의 타브는 기준이 아니다
    other = make_stem(version=2)
    _tab(db, other, 6, TabStatus.SUCCESS)
    assert sources.find_latest_tab_id(db, ready.id) == review.id


def test_request_saves_tab_id(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    tab = _tab(db, ready, 1, TabStatus.SUCCESS)
    c = service.request_comparison(db, WS, 500, enqueue=enqueue).comparison
    assert c.tab_id == tab.id


def test_playback_shows_tab_made_after_comparison(
    db: Session, ready: Stem, enqueue: EnqueueSpy, fake_storage: FakeStorage
) -> None:
    """비교 뒤에 만든 타브도 재생 응답에 기준 타브로 나온다 (볼 때마다 최신)."""
    c = service.request_comparison(db, WS, 500, enqueue=enqueue).comparison
    assert c.tab_id is None
    c.status = ComparisonStatus.NOT_COMPARABLE
    c.not_comparable_reason = "RERECORD_TOO_SHORT"
    db.commit()

    tab = _tab(db, ready, 1, TabStatus.SUCCESS)
    assert service.get_playback(db, WS, c.id).tab_id == tab.id

    tab.deleted_at = datetime.now(UTC)  # 지운 타브는 기준에서 빠진다
    db.commit()
    assert service.get_playback(db, WS, c.id).tab_id is None
