from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.base_entity import Base
from app.core.config import get_settings
from app.domains.processing import models, sources
from app.domains.processing.models import StemStage

TABLES = [models.Stem.__table__, models.StemStageResult.__table__]


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
    """작업 등록 대역. 등록 시점에 다른 세션에서 stem이 보이는지(commit 후 등록인지) 확인한다."""

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self.calls: list[tuple[int, StemStage]] = []
        self.session_factory = session_factory
        self.fail = False
        self.errors: list[str] = []  # _publish가 예외를 삼키므로 assert 대신 기록한다

    def __call__(self, stem_id: int, start: StemStage) -> None:
        if self.session_factory is not None:
            with self.session_factory() as other:
                if other.get(models.Stem, stem_id) is None:
                    self.errors.append(f"stem {stem_id}: commit 전에 등록함")
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append((stem_id, start))


@pytest.fixture
def enqueue(request: pytest.FixtureRequest) -> Iterator[EnqueueSpy]:
    factory = (
        request.getfixturevalue("session_factory")
        if "session_factory" in request.fixturenames
        else None
    )
    spy = EnqueueSpy(factory)
    yield spy
    assert spy.errors == []
