"""연주 비교의 기준 타브: 비교에 쓴 기타 음원으로 만든 최신 완료 타브 (이슈 #46)."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domains.compare import service, sources
from app.domains.processing.models import Stem
from app.domains.tab.models import Tab, TabStatus
from tests.domains.compare.conftest import WS
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
    done = _tab(db, ready, 1, TabStatus.SUCCESS)
    review = _tab(db, ready, 2, TabStatus.NEEDS_REVIEW)
    assert sources.find_latest_tab_id(db, ready.id) == review.id

    # 진행 중·실패·삭제된 더 새 버전은 건너뛴다
    _tab(db, ready, 3, TabStatus.QUEUED)
    _tab(db, ready, 4, TabStatus.FAILED)
    _tab(db, ready, 5, TabStatus.SUCCESS, deleted=True)
    assert sources.find_latest_tab_id(db, ready.id) == review.id
    assert done.id != review.id

    # 다른 기타 음원의 타브는 기준이 아니다
    other = make_stem(version=2)
    _tab(db, other, 6, TabStatus.SUCCESS)
    assert sources.find_latest_tab_id(db, ready.id) == review.id


def test_request_saves_tab_id(db: Session, ready: Stem, enqueue: EnqueueSpy) -> None:
    tab = _tab(db, ready, 1, TabStatus.SUCCESS)
    c = service.request_comparison(db, WS, 500, enqueue=enqueue).comparison
    assert c.tab_id == tab.id
