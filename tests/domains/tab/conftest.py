from collections.abc import Callable, Iterator

import pytest
from sqlalchemy.orm import Session

from app.domains.processing.models import Stem, StemStatus
from app.domains.tab.models import Tab
from tests.domains.conftest import EnqueueSpy, FakeSegments, make_enqueue

WS = 10


@pytest.fixture
def enqueue(request: pytest.FixtureRequest) -> Iterator[EnqueueSpy]:
    yield from make_enqueue(request, Tab)


@pytest.fixture
def ready_stem(db: Session, segments: FakeSegments) -> Callable[..., Stem]:
    """구간 1의 현재 기타 음원을 만든다 (기본: success, 구간 1000~5000ms)."""

    def make(status: StemStatus = StemStatus.SUCCESS, **kw: object) -> Stem:
        src = segments.add(segment_id=1)
        values: dict[str, object] = {
            "workspace_id": src.workspace_id,
            "track_id": src.track_id,
            "segment_id": src.segment_id,
            "start_ms": src.start_ms,
            "end_ms": src.end_ms,
            "version": 1,
            "status": status,
            "bypass_separation": False,
            "storage_key": "10/100/stems/1/1/guitar.wav",
            "input_pad_ms": 500,
            "input_hash": "s" * 64,
            "config_version": "2026-10-01",
        }
        stem = Stem(**(values | kw))
        db.add(stem)
        db.commit()
        return stem

    return make
