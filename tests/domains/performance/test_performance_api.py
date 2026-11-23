from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import storage
from app.core.database import get_db
from app.domains.account.models import TesterCode as _TesterCodeModel
from app.domains.account.models import Workspace
from app.domains.account.service import hash_token
from app.domains.input.models import Track
from app.domains.performance.models import Performance
from app.domains.segment.models import Segment
from app.main import app


@pytest.fixture
def client(
    session_factory: Callable[[], Session],
) -> Iterator[TestClient]:
    """테스트 DB를 사용하는 API 클라이언트."""

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def authed_workspace(
    client: TestClient,
    db: Session,
) -> Workspace:
    """테스트용 워크스페이스를 생성하고 로그인한다."""
    workspace = Workspace(owner_type="TESTER_CODE")
    db.add(workspace)
    db.flush()

    raw_code = "TESTER-2026-REC01"

    db.add(
        _TesterCodeModel(
            code_hash=hash_token(raw_code),
            workspace_id=workspace.id,
            status="ACTIVE",
        )
    )
    db.commit()

    response = client.post(
        "/api/v1/auth/tester-login",
        json={"tester_code": raw_code},
    )

    assert response.status_code == 200

    return workspace


def test_create_and_get_performance_api(
    client: TestClient,
    db: Session,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 REC API로 WebM 연주를 등록하고 목록을 조회한다."""

    # 원곡 생성
    track = Track(
        workspace_id=authed_workspace.id,
        filename="original.mp3",
        media_type="audio/mpeg",
        storage_key=f"{authed_workspace.id}/tracks/original.mp3",
        file_hash="originalhash",
        duration_ms=300_000,
        file_size_bytes=1024,
        is_separated_stem=False,
    )
    db.add(track)
    db.flush()

    # 연습 구간 생성
    segment = Segment(
        track_id=track.id,
        start_ms=1000,
        end_ms=5000,
        name="VERSE",
        version=1,
    )
    db.add(segment)
    db.commit()
    db.refresh(segment)

    # Object Storage 대역
    uploaded: list[dict[str, object]] = []

    def fake_put_bytes(
        key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        uploaded.append(
            {
                "key": key,
                "data": data,
                "content_type": content_type,
            }
        )

    monkeypatch.setattr(storage, "put_bytes", fake_put_bytes)

    # 실제 POST API 요청
    create_response = client.post(
        f"/api/v1/segments/{segment.id}/performances",
        files={
            "audio_file": (
                "blob",
                b"test webm audio data",
                "audio/webm;codecs=opus",
            )
        },
        data={"source": "RECORDING"},
    )

    assert create_response.status_code == 201, create_response.text

    create_body = create_response.json()

    assert create_body["success"] is True

    performance_data = create_body["data"]

    assert performance_data["workspace_id"] == authed_workspace.id
    assert performance_data["track_id"] == track.id
    assert performance_data["segment_id"] == segment.id
    assert performance_data["source"] == "RECORDING"
    assert performance_data["start_ms"] == 1000
    assert performance_data["end_ms"] == 5000

    # 내부 저장 경로는 API 응답에 노출하지 않는다.
    assert "storage_key" not in performance_data

    # Object Storage 저장 확인
    assert len(uploaded) == 1
    assert uploaded[0]["data"] == b"test webm audio data"
    assert uploaded[0]["content_type"] == "audio/webm"

    # DB 저장 확인
    performance = db.get(
        Performance,
        performance_data["performance_id"],
    )

    assert performance is not None
    assert performance.workspace_id == authed_workspace.id
    assert performance.storage_key == uploaded[0]["key"]

    # 실제 GET API 요청
    list_response = client.get(f"/api/v1/segments/{segment.id}/performances")

    assert list_response.status_code == 200, list_response.text

    list_body = list_response.json()

    assert list_body["success"] is True
    assert list_body["data"]["segment_id"] == segment.id

    performances = list_body["data"]["performances"]

    assert len(performances) == 1
    assert performances[0]["performance_id"] == performance.id
    assert performances[0]["source"] == "RECORDING"
    assert "storage_key" not in performances[0]
