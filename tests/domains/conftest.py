"""도메인 테스트 공용 픽스처: 테스트 전용 DB, 다른 도메인 대역, 작업 등록 대역."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.base_entity import Base
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.compare import models as compare_models
from app.domains.processing import models as processing_models
from app.domains.processing import sources

# FK 순서대로 (부모 먼저)
TABLES = [
    processing_models.Stem.__table__,
    processing_models.StemStageResult.__table__,
    compare_models.Comparison.__table__,
    compare_models.ComparisonStageResult.__table__,
    compare_models.ComparisonRegion.__table__,
]


def _test_database_url() -> URL:
    """DATABASE_URL과 같은 서버의 '<이름>_test' DB. 앱 DB의 데이터는 건드리지 않는다."""
    url = make_url(get_settings().database_url)
    name = url.database or "capstone"
    return url if name.endswith("_test") else url.set(database=f"{name}_test")


def _ensure_database(test_url: URL) -> None:
    admin = create_engine(test_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_url.database}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """테스트 전용 DB('<이름>_test')에 연결한다. Postgres가 없으면 DB 테스트를 건너뛴다."""
    test_url = _test_database_url()
    assert test_url.database and test_url.database.endswith("_test")
    try:
        _ensure_database(test_url)
    except OperationalError:
        pytest.skip("Postgres에 연결할 수 없어 DB 테스트를 건너뜁니다")
    engine = create_engine(test_url, pool_pre_ping=True)
    Base.metadata.drop_all(engine, tables=TABLES)  # 이전 실행의 스키마가 남아 있을 수 있음
    Base.metadata.create_all(engine, tables=TABLES)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine: Engine) -> Iterator[Callable[[], Session]]:
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    yield factory
    names = ", ".join(t.name for t in reversed(TABLES))
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


class FakeSegments:
    """processing.sources.load_segment 대역. 구간 범위를 바꿔 가며 테스트한다."""

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
    """작업 등록 대역. 등록 시점에 다른 세션에서 행이 보이는지(commit 후 등록인지) 확인한다."""

    def __init__(
        self, model: type[Any], session_factory: Callable[[], Session] | None = None
    ) -> None:
        self.model = model
        self.calls: list[tuple[int, Any]] = []
        self.session_factory = session_factory
        self.fail = False
        self.errors: list[str] = []  # _publish가 예외를 삼키므로 assert 대신 기록한다

    def __call__(self, row_id: int, start: Any) -> None:
        if self.session_factory is not None:
            with self.session_factory() as other:
                if other.get(self.model, row_id) is None:
                    self.errors.append(f"{self.model.__name__} {row_id}: commit 전에 등록함")
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append((row_id, start))


def make_enqueue(request: pytest.FixtureRequest, model: type[Any]) -> Iterator[EnqueueSpy]:
    """도메인별 enqueue 픽스처에서 쓴다. DB 테스트면 commit 후 등록인지도 확인한다."""
    factory = (
        request.getfixturevalue("session_factory")
        if "session_factory" in request.fixturenames
        else None
    )
    spy = EnqueueSpy(model, factory)
    yield spy
    assert spy.errors == []
