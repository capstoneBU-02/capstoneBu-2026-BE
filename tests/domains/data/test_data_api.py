from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.database import get_db
from app.domains.account.models import Workspace
from app.domains.data.models import FileAccessLog, MediaTask, TabResult
from app.domains.input.models import Track
from app.domains.processing import sources
from app.domains.processing.models import Stem, StemStatus
from app.domains.segment.models import Segment
from app.domains.tab.models import Tab, TabStatus
from app.main import create_app


class FakeStorage:
    """S3 저장소 인메모리 대역."""

    def __init__(self) -> None:
        self.files: set[str] = set()

    def file_exists(self, key: str) -> bool:
        return key in self.files

    def presigned_get_url(
        self,
        key: str,
        *,
        expires_in_seconds: int | None = None,
        download_name: str | None = None,
    ) -> str:
        exp = expires_in_seconds or 600
        disp = f"&name={download_name}" if download_name else ""
        return f"https://storage.test/{key}?X-Amz-Expires={exp}{disp}"


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(storage, "file_exists", fake.file_exists)
    monkeypatch.setattr(storage, "presigned_get_url", fake.presigned_get_url)
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
def seed_pipeline(db: Session, workspaces: tuple[Workspace, Workspace]) -> dict[str, object]:
    """트랙 -> 구간 -> 스템 -> 타브(완료/진행중/실패) 기본 데이터를 세팅한다."""
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
        storage_key=f"{ws1.id}/{track.id}/stems/{segment.id}/1/guitar.wav",
        input_hash="b" * 64,
        config_version="test",
    )
    db.add(stem)
    db.flush()

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

    db.add(
        MediaTask(
            task_id=tab_failed.id,
            workspace_id=ws1.id,
            track_id=track.id,
            segment_id=segment.id,
            parent_task_id=None,
            version=1,
            status="failed",
            is_reused=False,
        )
    )
    db.flush()
    db.add(
        MediaTask(
            task_id=tab_done.id,
            workspace_id=ws1.id,
            track_id=track.id,
            segment_id=segment.id,
            parent_task_id=tab_failed.id,
            version=2,
            status="completed",
            is_reused=False,
        )
    )
    db.add(
        TabResult(
            tab_id=tab_done.id,
            task_id=tab_done.id,
            segment_id=segment.id,
            workspace_id=ws1.id,
            file_key=f"{ws1.id}/tabs/{tab_done.id}/tab.musicxml",
            format="musicxml",
            file_size_bytes=24_000,
        )
    )
    db.commit()

    return {
        "track": track,
        "segment": segment,
        "stem": stem,
        "tab_failed": tab_failed,
        "tab_done": tab_done,
        "tab_processing": tab_processing,
    }


# ---- DATA-01: GET /api/v1/tabs/{tab_id}/download ----


def test_download_tab_success(
    api: TestClient,
    seed_pipeline: dict[str, object],
) -> None:
    track: Track = seed_pipeline["track"]  # type: ignore[assignment]
    segment: Segment = seed_pipeline["segment"]  # type: ignore[assignment]
    tab_done: Tab = seed_pipeline["tab_done"]  # type: ignore[assignment]

    res = api.get(f"/api/v1/tabs/{tab_done.id}/download")

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert data["tab_id"] == tab_done.id
    assert data["segment_id"] == segment.id
    assert data["format"] == "musicxml"
    assert data["file_name"] == f"track_{track.id}_segment_{segment.id}_v2.musicxml"
    assert "X-Amz-Expires=600" in data["download_url"]
    assert datetime.fromisoformat(data["expires_at"]) > datetime.now(UTC)


def test_download_tab_not_ready_returns_409(
    api: TestClient,
    seed_pipeline: dict[str, object],
) -> None:
    tab_processing: Tab = seed_pipeline["tab_processing"]  # type: ignore[assignment]

    res = api.get(f"/api/v1/tabs/{tab_processing.id}/download")

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "TAB_NOT_READY"


def test_download_tab_unsupported_format_returns_422(
    api: TestClient,
    seed_pipeline: dict[str, object],
) -> None:
    tab_done: Tab = seed_pipeline["tab_done"]  # type: ignore[assignment]

    res = api.get(f"/api/v1/tabs/{tab_done.id}/download", params={"format": "pdf"})

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "UNSUPPORTED_DOWNLOAD_FORMAT"


def test_download_tab_other_workspace_or_missing_returns_404(
    api: TestClient,
    workspaces: tuple[Workspace, Workspace],
    current_ws_id: list[int],
    seed_pipeline: dict[str, object],
) -> None:
    tab_done: Tab = seed_pipeline["tab_done"]  # type: ignore[assignment]

    # 존재하지 않는 타브 ID
    missing = api.get("/api/v1/tabs/999999/download")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TAB_NOT_FOUND"

    # 다른 워크스페이스로 전환 후 접근
    current_ws_id[0] = workspaces[1].id
    forbidden = api.get(f"/api/v1/tabs/{tab_done.id}/download")
    assert forbidden.status_code == 404
    assert forbidden.json()["error"]["code"] == "TAB_NOT_FOUND"


# ---- DATA-02: GET /api/v1/workspaces/tasks & GET /api/v1/tasks/{task_id} ----


def test_list_workspace_tasks_and_storage_usage(
    api: TestClient,
    seed_pipeline: dict[str, object],
) -> None:
    tab_failed: Tab = seed_pipeline["tab_failed"]  # type: ignore[assignment]
    tab_done: Tab = seed_pipeline["tab_done"]  # type: ignore[assignment]

    res = api.get("/api/v1/workspaces/tasks", params={"page": 1, "limit": 10})

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["pagination"] == {"page": 1, "limit": 10, "total_count": 3}

    # 스토리지 사용량 = track(5,000,000) + tab_result(24,000)
    assert data["storage"]["usage_bytes"] == 5_024_000
    assert (
        data["storage"]["storage_limit_exceeded"]
        is (5_024_000 > get_settings().workspace_max_storage_bytes)
    )

    items_by_id = {item["task_id"]: item for item in data["items"]}
    done_item = items_by_id[tab_done.id]
    assert done_item["parent_task_id"] == tab_failed.id
    assert done_item["version"] == 2
    assert done_item["track_filename"] == "guitar_sample.mp3"
    assert done_item["start_sec"] == 15.0
    assert done_item["end_sec"] == 75.0
    assert done_item["target_part"] == "guitar"
    assert done_item["priority"] == "riff"
    assert done_item["status"] == "completed"
    assert done_item["is_reused"] is False


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
    assert data["status"] == "completed"

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
    fake_storage.files.add(file_key)

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


def test_create_presigned_url_access_denied_and_not_found(
    api: TestClient,
    workspaces: tuple[Workspace, Workspace],
    fake_storage: FakeStorage,
) -> None:
    ws1, ws2 = workspaces
    other_key = f"{ws2.id}/tracks/999/secret.wav"
    fake_storage.files.add(other_key)

    # 1. 다른 워크스페이스 자원 요청 -> 403 FILE_ACCESS_DENIED
    denied = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": other_key, "access_type": "STREAM"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FILE_ACCESS_DENIED"

    # 2. 경로 탐색 시도 -> 403 FILE_ACCESS_DENIED
    traversal = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": f"{ws1.id}/../{ws2.id}/secret.wav", "access_type": "VIEW"},
    )
    assert traversal.status_code == 403
    assert traversal.json()["error"]["code"] == "FILE_ACCESS_DENIED"

    # 3. 내 워크스페이스 경로지만 실제 스토리지에 없는 파일 -> 404 FILE_NOT_FOUND
    missing = api.post(
        "/api/v1/files/presigned-url",
        json={"file_key": f"{ws1.id}/tracks/not_exists.wav", "access_type": "DOWNLOAD"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "FILE_NOT_FOUND"