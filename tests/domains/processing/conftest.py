from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.base_entity import Base
from app.core.config import get_settings
from app.domains.processing import models, sources
from app.domains.processing.models import StemStage

TABLES = [models.Stem.__table__, models.StemStageResult.__table__]


@pytest.fixture(scope="session")
def db_engine():  # type: ignore[no-untyped-def]
    """DATABASE_URL의 Postgres. 연결할 수 없으면 DB 테스트를 건너뛴다 (CI에는 있음)."""
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Postgres에 연결할 수 없어 DB 테스트를 건너뜁니다")
    Base.metadata.create_all(engine, tables=TABLES)  # 마이그레이션 전이어도 테이블 준비
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine) -> Iterator[Callable[[], Session]]:  # type: ignore[no-untyped-def]
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    yield factory
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE stem_stage_results, stems RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


class FakeSegments:
    """sources.load_segment 대역. 구간 범위를 바꿔 가며 테스트한다."""

    def __init__(self) -> None:
        self.segments: dict[int, sources.SegmentSource] = {}

    def add(self, segment_id: int = 1, **kw: object) -> sources.SegmentSource:
        values: dict[str, object] = {
            "workspace_id": 10,
            "track_id": 100,
            "segment_id": segment_id,
            "start_ms": 1000,
            "end_ms": 5000,
            "is_separated_stem": False,
            "track_storage_key": "10/100/original.mp3",
            "track_file_hash": "filehash",
        }
        values.update(kw)
        src = sources.SegmentSource(**values)  # type: ignore[arg-type]
        self.segments[segment_id] = src
        return src

    def load(self, db: Session, workspace_id: int, segment_id: int) -> sources.SegmentSource:
        from app.core.exceptions import BusinessException, ErrorCode

        src = self.segments.get(segment_id)
        if src is None or src.workspace_id != workspace_id:
            raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)
        return src


@pytest.fixture
def segments(monkeypatch: pytest.MonkeyPatch) -> FakeSegments:
    fake = FakeSegments()
    monkeypatch.setattr(sources, "load_segment", fake.load)
    return fake


class EnqueueSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[int, StemStage]] = []

    def __call__(self, stem_id: int, start: StemStage) -> None:
        self.calls.append((stem_id, start))


@pytest.fixture
def enqueue() -> EnqueueSpy:
    return EnqueueSpy()
