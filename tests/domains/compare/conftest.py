from collections.abc import Callable, Iterator

import pytest
from sqlalchemy.orm import Session

from app.core import storage
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.compare import sources
from app.domains.compare.models import Comparison
from app.domains.processing.models import Stem, StemStatus
from tests.domains.conftest import EnqueueSpy, FakeSegments, make_enqueue

WS = 10


@pytest.fixture
def enqueue(request: pytest.FixtureRequest) -> Iterator[EnqueueSpy]:
    yield from make_enqueue(request, Comparison)


class FakePerformances:
    """compare.sources.load_performance 대역."""

    def __init__(self) -> None:
        self.items: dict[int, sources.PerformanceSource] = {}

    def add(self, performance_id: int = 500, **kw: object) -> sources.PerformanceSource:
        values: dict[str, object] = {
            "performance_id": performance_id,
            "workspace_id": WS,
            "track_id": 100,
            "segment_id": 1,
            "start_ms": 1000,
            "end_ms": 5000,
            "storage_key": f"10/performances/{performance_id}.wav",
            "file_hash": f"perf{performance_id}",
        }
        values.update(kw)
        p = sources.PerformanceSource(**values)  # type: ignore[arg-type]
        self.items[performance_id] = p
        return p

    def load(
        self, db: Session, workspace_id: int, performance_id: int
    ) -> sources.PerformanceSource:
        p = self.items.get(performance_id)
        if p is None or p.workspace_id != workspace_id:
            raise BusinessException(ErrorCode.PERFORMANCE_NOT_FOUND)
        return p


@pytest.fixture
def performances(monkeypatch: pytest.MonkeyPatch) -> FakePerformances:
    fake = FakePerformances()
    monkeypatch.setattr(sources, "load_performance", fake.load)
    return fake


class FakeStorage:
    """저장소 대역 (메모리). 업로드·다운로드·presigned URL."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def read_bytes(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        if key not in self.files:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return self.files[key]

    def download_file(self, key: str, path: str) -> None:
        from botocore.exceptions import ClientError

        if key not in self.files:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        with open(path, "wb") as f:
            f.write(self.files[key])

    def presigned_download_url(self, key: str) -> str:
        return f"https://storage.test/{key}?sig=1"


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    for name in ("put_bytes", "read_bytes", "download_file", "presigned_download_url"):
        monkeypatch.setattr(storage, name, getattr(fake, name))
    return fake


@pytest.fixture
def make_stem(db: Session) -> Callable[..., Stem]:
    """현재 구간 범위의 기타 음원을 바로 만든다 (JOB을 거치지 않음)."""

    def _make(
        status: StemStatus = StemStatus.SUCCESS,
        start_ms: int = 1000,
        end_ms: int = 5000,
        version: int = 1,
        bypass: bool = False,
    ) -> Stem:
        stem = Stem(
            workspace_id=WS,
            track_id=100,
            segment_id=1,
            start_ms=start_ms,
            end_ms=end_ms,
            version=version,
            part="guitar",
            status=status,
            bypass_separation=bypass,
            storage_key=f"10/100/stems/1/v{version}/guitar.wav",
            input_hash=f"stemhash{version}",
            config_version="test",
            retry_count=0,
        )
        db.add(stem)
        db.commit()
        return stem

    return _make


@pytest.fixture
def ready(
    segments: FakeSegments,
    performances: FakePerformances,
    make_stem: Callable[..., Stem],
) -> Stem:
    """비교할 수 있는 기본 상태: 구간 1(1000~5000), 같은 범위 연주 500, 성공한 기타 음원."""
    segments.add(segment_id=1, name="VERSE")
    performances.add(500)
    return make_stem()
