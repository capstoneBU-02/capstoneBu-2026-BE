"""도메인 테스트 공용 픽스처: 테스트 전용 DB, 다른 도메인 대역, 작업 등록 대역."""

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.core.base_entity import Base
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.account import models as account_models
from app.domains.compare import models as compare_models
from app.domains.data import models as data_models
from app.domains.input import models as input_models
from app.domains.input.models import Track
from app.domains.processing import models as processing_models
from app.domains.processing import sources
from app.domains.segment.models import Segment, SegmentHistory
from app.domains.tab import models as tab_models

# FK 순서대로 (부모 먼저)
TABLES = [
    account_models.Workspace.__table__,
    account_models.TesterCode.__table__,
    account_models.WorkspaceSession.__table__,
    input_models.Track.__table__,
    Segment.__table__,
    SegmentHistory.__table__,
    processing_models.Stem.__table__,
    processing_models.StemStageResult.__table__,
    tab_models.Tab.__table__,
    tab_models.TabStageResult.__table__,
    compare_models.Comparison.__table__,
    compare_models.ComparisonStageResult.__table__,
    compare_models.ComparisonRegion.__table__,
    data_models.MediaTask.__table__,
    data_models.TabResult.__table__,
    data_models.FileAccessLog.__table__,
]


# 테스트가 쓰는 워크스페이스 (stems·comparisons.workspace_id FK).
# 자동 증가 번호(1부터)와 겹치지 않게 큰 값.
TEST_WORKSPACE_IDS = (10, 99)


def _test_database_url() -> URL:
    """DATABASE_URL과 같은 서버의 '<이름>_test' DB. 앱 DB의 데이터는 건드리지 않는다."""
    url = make_url(get_settings().database_url)
    name = url.database or "capstone"
    return url if name.endswith("_test") else url.set(database=f"{name}_test")


# Postgres 오류 코드 (SQLSTATE). 접속 단계 오류에는 붙지 않는다
_DUPLICATE_DATABASE = "42P04"  # 동시에 두 번 만들려다 이미 있음
_INSUFFICIENT_PRIVILEGE = "42501"  # 권한 없음 (DB·테이블 생성)


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def _first_line(exc: DBAPIError) -> str:
    lines = str(exc.orig or exc).strip().splitlines()
    return lines[0] if lines else type(exc.orig or exc).__name__


def _server_reachable(test_url: URL, timeout: float = 3.0) -> bool:
    """Postgres 포트가 열려 있는지. Windows는 닫힌 포트에서 접속 시간 초과까지 오래 기다린다."""
    if not test_url.host or test_url.host.startswith("/"):  # 유닉스 소켓은 접속해 보며 판단
        return True
    try:
        with socket.create_connection((test_url.host, test_url.port or 5432), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_database(test_url: URL) -> None:
    """테스트 DB가 없으면 만든다.

    'postgres' DB에 접속하지 못하면(권한 없음 등) 테스트 DB에 바로 접속해 본다.
    관리자가 미리 만들어 준 테스트 DB만 쓸 수 있는 계정도 테스트를 돌릴 수 있게 한다.
    """
    admin = create_engine(test_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_url.database}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
        return
    except ProgrammingError as exc:
        if _sqlstate(exc) == _DUPLICATE_DATABASE:  # 다른 실행이 먼저 만들었으면 그대로 쓴다
            return
        raise
    except OperationalError:
        pass
    finally:
        admin.dispose()

    direct = create_engine(test_url)
    try:
        with direct.connect():
            return
    finally:
        direct.dispose()


NO_PERMISSION_GUIDE = """
테스트 DB '{db}'를 만들거나 그 안에 테이블을 만들 권한이 없어 테스트를 멈춥니다. (계정: {user})

테스트는 .env의 DATABASE_URL과 같은 서버에 '<DB이름>_test' DB를 만들어 씁니다.
아래 중 하나로 해결해 주세요.

  1) 추천: 프로젝트 Docker Postgres 사용 (계정이 관리자라 자동으로 만들어짐)
       docker compose up -d postgres redis seaweedfs
       DATABASE_URL=postgresql+psycopg://capstone:capstone@localhost:5432/capstone
  2) 지금 Postgres의 관리자 계정으로 한 번만 실행
       psql -U postgres -c 'ALTER ROLE "{user}" CREATEDB;'
     (또는 CREATE DATABASE "{db}" OWNER "{user}"; 로 만들어 주기.
      이미 다른 계정 소유로 있으면 지우고 다시 만들어 주세요)

팀 공용 서버 DB에서는 테스트를 돌리지 마세요.
원래 오류: {err}
"""

CONNECT_FAILED_GUIDE = """
Postgres는 켜져 있지만 접속하지 못해 테스트를 멈춥니다. (계정: {user}, 테스트 DB: {db})
.env의 DATABASE_URL 계정·비밀번호를 확인해 주세요.
계정에 DB 생성 권한이 없다면 프로젝트 Docker Postgres를 쓰는 것이 가장 쉽습니다.
  docker compose up -d postgres redis seaweedfs
원래 오류: {err}
"""


def _prepare_test_database(test_url: URL) -> Engine:
    """테스트 DB와 테이블을 준비한다.

    - Postgres 포트가 닫혀 있으면(꺼져 있음) DB 테스트를 건너뛴다.
    - 켜져 있는데 권한·접속 문제가 있으면 해결 방법을 보여 주고 멈춘다. 그냥 두면 DB 테스트마다
      같은 오류가 나서(100개 이상) 원인을 찾기 어렵고, 건너뛰면 통과한 것처럼 보인다.
    """
    if not _server_reachable(test_url):
        pytest.skip("Postgres에 연결할 수 없어 DB 테스트를 건너뜁니다")
    fill = {"db": test_url.database, "user": test_url.username}
    engine: Engine | None = None
    try:
        _ensure_database(test_url)
        engine = create_engine(test_url, pool_pre_ping=True)
        Base.metadata.drop_all(engine, tables=TABLES)  # 이전 실행의 스키마가 남아 있을 수 있음
        Base.metadata.create_all(engine, tables=TABLES)
        return engine
    except DBAPIError as exc:
        if engine is not None:
            engine.dispose()
        if _sqlstate(exc) == _INSUFFICIENT_PRIVILEGE:
            pytest.exit(NO_PERMISSION_GUIDE.format(**fill, err=_first_line(exc)), returncode=1)
        if isinstance(exc, OperationalError):  # 서버는 켜져 있는데 접속 실패
            pytest.exit(CONNECT_FAILED_GUIDE.format(**fill, err=_first_line(exc)), returncode=1)
        raise


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """테스트 전용 DB('<이름>_test')에 연결한다. Postgres가 꺼져 있으면 DB 테스트를 건너뛴다."""
    test_url = _test_database_url()
    assert test_url.database and test_url.database.endswith("_test")
    engine = _prepare_test_database(test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine: Engine) -> Iterator[Callable[[], Session]]:
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    with db_engine.begin() as conn:
        for ws_id in TEST_WORKSPACE_IDS:
            conn.execute(
                text("INSERT INTO workspaces (id, owner_type) VALUES (:id, 'TESTER_CODE')"),
                {"id": ws_id},
            )
    yield factory
    names = ", ".join(t.name for t in reversed(TABLES))
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


class FakeSegments:
    """구간 대역. DB 테스트면 실제 tracks·segments 행을 넣는다 (DB 없는 테스트만 트랙 조회 대역).

    같은 segment_id로 다시 add하면 구간 수정처럼 범위를 바꾸고 version을 올린다.
    트랙은 track_id 단위라, 다른 워크스페이스의 구간을 만들 때는 track_id도 다르게 준다.
    """

    def __init__(self, session_factory: Callable[[], Session] | None) -> None:
        self.session_factory = session_factory
        self.tracks: dict[int, sources.TrackSource] = {}

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
        if self.session_factory is None:  # DB 없는 테스트만 트랙 조회 대역을 쓴다
            self.tracks[src.track_id] = sources.TrackSource(
                track_id=src.track_id,
                workspace_id=src.workspace_id,
                is_separated_stem=src.is_separated_stem,
                storage_key=src.track_storage_key,
                file_hash=src.track_file_hash,
            )
        else:
            with self.session_factory() as db:
                db.merge(
                    Track(
                        id=src.track_id,
                        workspace_id=src.workspace_id,
                        filename="original.mp3",
                        media_type="audio/mpeg",
                        storage_key=src.track_storage_key,
                        file_hash=src.track_file_hash,
                        duration_ms=300_000,
                        file_size_bytes=1,
                        is_separated_stem=src.is_separated_stem,
                    )
                )
                db.flush()
                row = db.get(Segment, segment_id)
                if row is None:
                    row = Segment(id=segment_id, version=1)
                    db.add(row)
                elif (row.start_ms, row.end_ms) != (src.start_ms, src.end_ms):
                    row.version += 1  # SEG처럼 범위가 바뀌면 version을 올린다
                row.track_id = src.track_id
                row.start_ms, row.end_ms, row.name = src.start_ms, src.end_ms, src.name
                db.commit()
                # id를 직접 넣었으므로 시퀀스를 따라 올려 이후 자동 증가 id와 겹치지 않게 한다
                for table in ("tracks", "segments"):
                    db.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"(SELECT max(id) FROM {table}))"
                        )
                    )
                db.commit()
        return src

    def load_track(self, db: Session, track_id: int) -> sources.TrackSource:
        track = self.tracks.get(track_id)
        if track is None:
            raise BusinessException(ErrorCode.TRACK_NOT_FOUND)
        return track


@pytest.fixture
def segments(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> FakeSegments:
    factory = (
        request.getfixturevalue("session_factory")
        if "session_factory" in request.fixturenames
        else None
    )
    fake = FakeSegments(factory)
    if factory is None:  # DB 테스트는 실제 tracks 조회 경로를 쓴다
        monkeypatch.setattr(sources, "load_track", fake.load_track)
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
