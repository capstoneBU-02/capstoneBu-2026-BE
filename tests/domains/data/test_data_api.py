import importlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.database import get_db
from app.domains.account.models import Workspace
from app.domains.compare.models import Comparison, ComparisonStatus
from app.domains.data.models import FileAccessLog
from app.domains.input.models import Track
from app.domains.processing import sources
from app.domains.processing.models import Stem, StemStatus
from app.domains.segment.models import Segment
from app.domains.tab import keys as tab_keys
from app.domains.tab.models import Tab, TabStatus
from app.main import create_app

try:
    Performance = importlib.import_module("app.domains.performance.models").Performance
except ImportError:
    Performance = None


class FakeStorage:
    """S3 저장소 인메모리 대역 (키별 바이트 크기 지원)."""

    def __init__(self) -> None:
        self.files: dict[str, int] = {}

    def put(self, key: str, size_bytes: int = 1024) -> None:
        self.files[key] = size_bytes

    def file_exists(self, key: str) -> bool:
        return key in self.files

    def object_size(self, key: str) -> int:
        return self.files.get(key, 0)

    def presigned_download_url(
        self,
        key: str,
        download_name: str | None = None,
        *,
        expires_in_seconds: int | None = None,
    ) -> str:
        exp = expires_in_seconds or 600
        disp = f"&name={download_name}" if download_name else ""
        return f"https://storage.test/{key}?X-Amz-Expires={exp}{disp}"


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(storage, "file_exists", fake.file_exists)
    monkeypatch.setattr(storage, "object_size", fake.object_size)
    monkeypatch.setattr(storage, "presigned_download_url", fake.presigned_download_url)
    return fake


@pytest.fixture
def workspaces(db: Session) -> tuple[Workspace, Workspace]:
    ws1 = Workspace(owner_type="TESTER_CODE")
    ws2 = Workspace(owner_type="TESTER_CODE")
    db.add_all([ws1, ws2])
    db.commit()
    return ws1, ws2


@pytest.fixture
def current_ws_id(workspaces: tuple[Workspace, Workspace]) -> list[int]:
    """테스트 중간에 현재 인증된 워크스페이스를 전환할 수 있도록 리스트로 감싼다."""
    return [workspaces[0].id]


@pytest.fixture
def api(
    session_factory: Callable[[], Session],
    current_ws_id: list[int],
    fake_storage: FakeStorage,
) -> Iterator[TestClient]:
    app = create_app()

    def _db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[sources.get_current_workspace_id] = lambda: current_ws_id[0]
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def seed_pipeline(
    db: Session,
    workspaces: tuple[Workspace, Workspace],
    fake_storage: FakeStorage,
) -> dict[str, object]:
    """트랙 -> 구간 -> 스템 -> (연주) -> 타브(완료/진행중/실패) -> 비교 기본 데이터를 세팅한다."""
    ws1, _ = workspaces

    track = Track(
        workspace_id=ws1.id,
        filename="guitar_sample.mp3",
        media_type="audio/mpeg",
        storage_key=f"{ws1.id}/tracks/sample.mp3",
        file_hash="a" * 64,
        duration_ms=180_500,
        file_size_bytes=5_000_000,
        is_separated_stem=False,
    )
    db.add(track)
    db.flush()

    segment = Segment(
        track_id=track.id,
        start_ms=15_000,
        end_ms=75_000,
        name="RIFF",
        version=1,
    )
    db.add(segment)
    db.flush()

    stem_key = f"{ws1.id}/{track.id}/stems/{segment.id}/1/guitar.wav"
    fake_storage.put(stem_key, 300_000)

    stem = Stem(
        workspace_id=ws1.id,
        track_id=track.id,
        segment_id=segment.id,
        start_ms=15_000,
        end_ms=75_000,
        version=1,
        part="guitar",
        status=StemStatus.SUCCESS,
        bypass_separation=False,
        storage_key=stem_key,
        input_hash="b" * 64,
        config_version="test",
    )
    db.add(stem)
    db.flush()

    perf_id = 1
    perf_bytes = 0
    if Performance is not None:
        perf_key = f"{ws1.id}/{track.id}/performances/perf_1.wav"
        fake_storage.put(perf_key, 200_000)
        performance = Performance(
            workspace_id=ws1.id,
            track_id=track.id,
            segment_id=segment.id,
            source="RECORDING",
            storage_key=perf_key,
            file_hash="p" * 64,
            start_ms=15_000,
            end_ms=75_000,
        )
        db.add(performance)
        db.flush()
        perf_id = performance.id
        perf_bytes = 200_000

    tab_failed = Tab(
        workspace_id=ws1.id,
        track_id=track.id,
        segment_id=segment.id,
        stem_id=stem.id,
        version=1,
        result_type="solo",
        tuning_id="e_standard",
        capo=0,
        status=TabStatus.FAILED,
        input_hash="c" * 64,
        config_version="test",
    )
    tab_done = Tab(
        workspace_id=ws1.id,
        track_id=track.id,
        segment_id=segment.id,
        stem_id=stem.id,
        version=2,
        result_type="riff",
        tuning_id="e_standard",
        capo=0,
        status=TabStatus.SUCCESS,
        input_hash="d" * 64,
        config_version="test",
    )
    tab_processing = Tab(
        workspace_id=ws1.id,
        track_id=track.id,
        segment_id=segment.id,
        stem_id=stem.id,
        version=3,
        result_type="riff",
        tuning_id="e_standard",
        capo=0,
        status=TabStatus.PROCESSING,
        input_hash="e" * 64,
        config_version="test",
    )
    db.add_all([tab_failed, tab_done, tab_processing])
    db.flush()

    fake_storage.put(tab_keys.musicxml_key(tab_done), 20_000)
    fake_storage.put(tab_keys.tab_json_key(tab_done), 4_000)

    comparison = Comparison(
        workspace_id=ws1.id,
        track_id=track.id,
        performance_id=perf_id,
        segment_id=segment.id,
        stem_id=stem.id,
        tab_id=tab_done.id,
        start_ms=15_000,
        end_ms=75_000,
        status=ComparisonStatus.SUCCESS,
        input_hash="f" * 64,
        config_version="test",
    )
    db.add(comparison)
    db.commit()

    return {
        "track": track,
        "segment": segment,
        "stem": stem,
        "perf_bytes": perf_bytes,
        "tab_failed": tab_failed,
        "tab_done": tab_done,
        "tab_processing": tab_processing,
        "comparison": comparison,
    }


# ---- DATA-02: GET /api/v1/workspaces/tasks & GET /api/v1/tasks/{task_id} ----


def test_list_workspace_tasks_and_storage_usage(
    api: TestClient,
    seed_pipeline: dict[str, object],
) -> None:
    tab_done: Tab = seed_pipeline["tab_done"]  # type: ignore[assignment]
    perf_bytes: int = seed_pipeline["perf_bytes"]  # type: ignore[assignment]

    res = api.get("/api/v1/workspaces/tasks", params={"page": 1, "limit": 10})

    assert res.status_code == 200
    data = res.json()["data"]
    # 타브(3) + 스템(1) + 비교(1) = 총 5건
    assert data["pagination"] == {"page": 1, "limit": 10, "total_count": 5}

    # 스토리지 사용량 = track(5,000,000) + stem(300,000) + tab(24,000) + performance(머지 후 200,000)
    expected_bytes = 5_324_000 + perf_bytes
    assert data["storage"]["usage_bytes"] == expected_bytes
    assert (
        data["storage"]["storage_limit_exceeded"]
        is (expected_bytes > get_settings().workspace_max_storage_bytes)
    )

    tab_items = [item for item in data["items"] if item["task_type"] == "tab"]
    done_item = next(item for item in tab_items if item["task_id"] == tab_done.id)
    assert done_item["version"] == 2
    assert done_item["track_filename"] == "guitar_sample.mp3"
    assert done_item["start_sec"] == 15.0
    assert done_item["end_sec"] == 75.0
    assert done_item["target_part"] == "guitar"
    assert done_item["priority"] == "riff"
    assert done_item["status"] == "success"
    assert done_item["is_reused"] is False

    task_types = {item["task_type"] for item in data["items"]}
    assert task_types == {"tab", "stem", "comparison"}


def test_get_task_detail_success_and_not_found(
    api: TestClient,
    workspaces: tuple[Workspace, Workspace],
    current_ws_id: list[int],
    seed_pipeline: dict[str, object],
) -> None:
    tab_done: Tab = seed_pipeline["tab_done"]  # type: ignore[assignment]
    stem: Stem = seed_pipeline["stem"]  # type: ignore[assignment]

    res = api.get(f"/api/v1/tasks/{tab_done.id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["task_id"] == tab_done.id
    assert data["tab_id"] == tab_done.id
    assert data["stem_id"] == stem.id
    assert data["version"] == 2
    assert data["start_sec"] == 15.0
    assert data["end_sec"] == 75.0
    assert data["tuning_id"] == "e_standard"
    assert data["capo"] == 0
    assert data["status"] == "success"

    # 다른 워크스페이스에서 조회 시 404 TASK_NOT_FOUND
    current_ws_id[0] = workspaces[1].id
    other_res = api.get(f"/api/v1/tasks/{tab_done.id}")
    assert other_res.status_code == 404
    assert other_res.json()["error"]["code"] == "TASK_NOT_FOUND"


# ---- DATA-05: POST /api/v1/files/presigned-url ----


def test_create_presigned_url_success_and_logs_access(
    api: TestClient,
    db: Session,
    workspaces: tuple[Workspace, Workspace],
    fake_storage: FakeStorage,
) -> None:
    ws1, _ = workspaces
    file_key = f"{ws1.id}/tracks/102/stems/guitar.wav"
    fake_storage.put(file_key, 4096)

    res = api.post(
        "/api/v1/files/presigned-url",
        json={
            "file_key": file_key,
            "access_type": "STREAM",
            "expires_in_seconds": 600,
        },
    )

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["file_key"] == file_key
    assert "X-Amz-Expires=600" in data["presigned_url"]
    assert datetime.fromisoformat(data["expires_at"]) > datetime.now(UTC)

    logs = db.scalars(
        select(FileAccessLog).where(FileAccessLog.workspace_id == ws1.id)
    ).all()
    assert len(logs) == 1
    assert logs[0].file_key == file_key
    assert logs[0].access_type == "STREAM"


def test_create_presigned_url_access_denied_and_deleted_resource(
    api: TestClient,
    db: Session,
    workspaces: tuple[Workspace, Workspace],
    seed_pipeline: dict[str, object],
    fake_storage: FakeStorage,
) -> None:
    ws1, ws2 = workspaces
    stem: Stem = seed_pipeline["stem"]  # type: ignore[assignment]
    other_key = f"{ws2.id}/tracks/999/secret.wav"
    fake_storage.put(other_key)

    # 1. 다른 워크스페이스 자원 요청 -> 403 FILE_ACCESS_DENIED
    denied = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": other_key, "access_type": "STREAM"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FILE_ACCESS_DENIED"

    # 2. 구형 ws_{id}/ 접두사 차단 -> 403 FILE_ACCESS_DENIED
    legacy_key = f"ws_{ws1.id}/tracks/102/stems/guitar.wav"
    fake_storage.put(legacy_key)
    legacy_denied = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": legacy_key, "access_type": "STREAM"},
    )
    assert legacy_denied.status_code == 403
    assert legacy_denied.json()["error"]["code"] == "FILE_ACCESS_DENIED"

    # 3. 경로 탐색 시도 -> 403 FILE_ACCESS_DENIED
    traversal = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": f"{ws1.id}/../{ws2.id}/secret.wav", "access_type": "VIEW"},
    )
    assert traversal.status_code == 403
    assert traversal.json()["error"]["code"] == "FILE_ACCESS_DENIED"

    # 4. 소프트 삭제된 스템의 파일 키 요청 차단 -> 404 FILE_NOT_FOUND
    stem.deleted_at = datetime.now(UTC)
    db.commit()
    deleted_res = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": stem.storage_key, "access_type": "DOWNLOAD"},
    )
    assert deleted_res.status_code == 404
    assert deleted_res.json()["error"]["code"] == "FILE_NOT_FOUND"