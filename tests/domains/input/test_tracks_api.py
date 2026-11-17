from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import storage
from app.core.database import get_db
from app.domains.account.models import TesterCode, Workspace
from app.domains.account.service import hash_token
from app.domains.input import service as input_service
from app.domains.input.models import Track
from app.domains.input.schemas import InputSegmentCreateRequest
from app.domains.processing import service as processing_service
from app.domains.processing.models import Stem, StemStatus
from app.domains.segment.models import Segment
from app.main import app


@pytest.fixture
def client(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    input_service.reset_tab_jobs()

    uploaded_objects: dict[str, bytes] = {}

    def fake_put_bytes(key: str, data: bytes, content_type: str) -> None:
        uploaded_objects[key] = data

    monkeypatch.setattr(storage, "put_bytes", fake_put_bytes)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    input_service.reset_tab_jobs()


@pytest.fixture
def authed_workspace(client: TestClient, db: Session) -> Workspace:
    workspace = Workspace(owner_type="TESTER_CODE")
    db.add(workspace)
    db.flush()

    raw_code = "TESTER-2026-IN01"
    db.add(
        TesterCode(
            code_hash=hash_token(raw_code),
            workspace_id=workspace.id,
            status="ACTIVE",
        )
    )
    db.commit()

    res = client.post("/api/v1/auth/tester-login", json={"tester_code": raw_code})
    assert res.status_code == 200
    return workspace


def _mock_audio_duration(monkeypatch: pytest.MonkeyPatch, duration_sec: float) -> None:
    def fake_mutagen(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(info=SimpleNamespace(length=duration_sec))

    monkeypatch.setattr(input_service, "MutagenFile", fake_mutagen)


def test_upload_mp3_and_mp4_success(
    client: TestClient,
    db: Session,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_audio_duration(monkeypatch, 120.5)

    res_mp3 = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("song.mp3", b"dummy-mp3-bytes", "audio/mpeg")},
        data={"is_separated_stem": "false"},
    )
    assert res_mp3.status_code == 201
    body = res_mp3.json()
    assert body["success"] is True
    assert body["data"]["filename"] == "song.mp3"
    assert body["data"]["duration_ms"] == 120_500
    assert body["data"]["media_type"] == "audio/mpeg"
    assert body["data"]["is_separated_stem"] is False

    track = db.get(Track, body["data"]["track_id"])
    assert track is not None
    assert track.workspace_id == authed_workspace.id
    assert len(track.file_hash) == 64
    assert track.storage_key.startswith(f"{authed_workspace.id}/tracks/")

    res_mp4 = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("clip.mp4", b"dummy-mp4-bytes", "video/mp4")},
        data={"is_separated_stem": "true"},
    )
    assert res_mp4.status_code == 201
    assert res_mp4.json()["data"]["media_type"] == "video/mp4"
    assert res_mp4.json()["data"]["is_separated_stem"] is True


def test_upload_unsupported_format_and_duration_exceeded(
    client: TestClient,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    res_wav = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("bad.wav", b"wav-bytes", "audio/wav")},
    )
    assert res_wav.status_code == 400
    assert res_wav.json()["error"]["code"] == "UNSUPPORTED_FORMAT"

    _mock_audio_duration(monkeypatch, 300.5)
    res_long = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("long.mp3", b"long-bytes", "audio/mpeg")},
    )
    assert res_long.status_code == 400
    assert res_long.json()["error"]["code"] == "DURATION_EXCEEDED"


def test_separated_stem_sets_bypass_separation_true(
    client: TestClient,
    db: Session,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_audio_duration(monkeypatch, 60.0)
    monkeypatch.setattr(processing_service, "_default_enqueue", lambda stem_id, start: None)

    upload_res = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("guitar_only.mp3", b"guitar-bytes", "audio/mpeg")},
        data={"is_separated_stem": "true"},
    )
    assert upload_res.status_code == 201
    track_id = upload_res.json()["data"]["track_id"]

    seg = input_service.create_segment(
        db=db,
        workspace_id=authed_workspace.id,
        track_id=track_id,
        payload=InputSegmentCreateRequest(name="Intro", start_ms=0, end_ms=30_000),
    )
    assert seg.track_id == track_id

    stem_res, is_reused = input_service.request_stem_separation(
        db=db,
        workspace_id=authed_workspace.id,
        segment_id=seg.segment_id,
    )
    assert is_reused is False
    assert stem_res.bypass_separation is True


def test_tab_request_stem_not_ready_and_reuse(
    client: TestClient,
    db: Session,
    authed_workspace: Workspace,
) -> None:
    track = Track(
        workspace_id=authed_workspace.id,
        filename="test.mp3",
        media_type="audio/mpeg",
        storage_key=f"{authed_workspace.id}/tracks/test.mp3",
        file_hash="hash123",
        duration_ms=60_000,
        file_size_bytes=1024,
        is_separated_stem=False,
    )
    db.add(track)
    db.flush()

    segment = Segment(track_id=track.id, start_ms=0, end_ms=10_000, version=1)
    db.add(segment)
    db.commit()

    res_no_stem = client.post(
        f"/api/v1/segments/{segment.id}/tabs",
        json={"priority": "solo", "tuning_id": "e_standard", "capo": 0},
    )
    assert res_no_stem.status_code == 409
    assert res_no_stem.json()["error"]["code"] == "STEM_NOT_READY"

    stem = Stem(
        workspace_id=authed_workspace.id,
        track_id=track.id,
        segment_id=segment.id,
        start_ms=0,
        end_ms=10_000,
        version=1,
        status=StemStatus.SUCCESS,
        bypass_separation=False,
        input_hash="stemhash",
        config_version="2026-10-01",
    )
    db.add(stem)
    db.commit()

    first_tab = client.post(
        f"/api/v1/segments/{segment.id}/tabs",
        json={"priority": "solo", "tuning_id": "e_standard", "capo": 0},
    )
    assert first_tab.status_code == 202
    assert first_tab.json()["data"]["is_reused"] is False
    assert first_tab.json()["data"]["version"] == 1

    reused_tab = client.post(
        f"/api/v1/segments/{segment.id}/tabs",
        json={"priority": "solo", "tuning_id": "e_standard", "capo": 0},
    )
    assert reused_tab.status_code == 202
    assert reused_tab.json()["data"]["is_reused"] is True
    assert reused_tab.json()["data"]["version"] == 1
