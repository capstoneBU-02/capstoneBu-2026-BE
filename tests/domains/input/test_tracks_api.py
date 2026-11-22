from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.database import get_db
from app.domains.account.models import TesterCode, Workspace
from app.domains.account.service import hash_token
from app.domains.input import service as input_service
from app.domains.input.models import Track
from app.main import app


@pytest.fixture
def client(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
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


def test_upload_validation_errors(
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

    _mock_audio_duration(monkeypatch, 0.0)
    res_zero = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("zero.mp3", b"zero-bytes", "audio/mpeg")},
    )
    assert res_zero.status_code == 400
    assert res_zero.json()["error"]["code"] == "UNSUPPORTED_FORMAT"

    _mock_audio_duration(monkeypatch, 300.5)
    res_long = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("long.mp3", b"long-bytes", "audio/mpeg")},
    )
    assert res_long.status_code == 400
    assert res_long.json()["error"]["code"] == "DURATION_EXCEEDED"


def test_upload_file_too_large_returns_413(
    client: TestClient,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_max_bytes", 10)

    res_large = client.post(
        "/api/v1/tracks/upload",
        files={"file": ("huge.mp3", b"01234567890", "audio/mpeg")},
    )
    assert res_large.status_code == 413
    assert res_large.json()["error"]["code"] == "FILE_TOO_LARGE"


def _upload(client: TestClient, name: str) -> int:
    res = client.post(
        "/api/v1/tracks/upload",
        files={"file": (name, b"dummy-" + name.encode(), "audio/mpeg")},
    )
    assert res.status_code == 201, res.text
    return int(res.json()["data"]["track_id"])


def _other_workspace_track(db: Session) -> Track:
    other = Workspace(owner_type="TESTER_CODE")
    db.add(other)
    db.flush()
    track = Track(
        workspace_id=other.id,
        filename="other.mp3",
        media_type="audio/mpeg",
        storage_key=f"{other.id}/tracks/other.mp3",
        file_hash="otherhash",
        duration_ms=60_000,
        file_size_bytes=10,
        is_separated_stem=False,
    )
    db.add(track)
    db.commit()
    return track


def test_list_and_get_tracks(
    client: TestClient,
    db: Session,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.get("/api/v1/tracks").json()["data"] == {"items": []}

    _mock_audio_duration(monkeypatch, 120.0)
    first = _upload(client, "first.mp3")
    second = _upload(client, "second.mp3")
    for start in (30_000, 0):
        res = client.post(
            f"/api/v1/tracks/{first}/segments", json={"start_ms": start, "end_ms": start + 10_000}
        )
        assert res.status_code == 201, res.text
    _other_workspace_track(db)  # 다른 워크스페이스 곡은 목록에 나오지 않는다

    items = client.get("/api/v1/tracks").json()["data"]["items"]
    assert [(t["track_id"], t["segment_count"]) for t in items] == [(second, 0), (first, 2)]
    assert items[1]["filename"] == "first.mp3" and items[1]["duration_ms"] == 120_000

    detail = client.get(f"/api/v1/tracks/{first}").json()["data"]
    assert detail == items[1]


def test_track_of_other_workspace_is_not_found(
    client: TestClient, db: Session, authed_workspace: Workspace
) -> None:
    other = _other_workspace_track(db)
    for path in (
        f"/api/v1/tracks/{other.id}",
        f"/api/v1/tracks/{other.id}/segments",
        "/api/v1/tracks/999999",
    ):
        res = client.get(path)
        assert res.status_code == 404, path
        assert res.json()["error"]["code"] == "TRACK_NOT_FOUND"


def test_list_and_get_segments(
    client: TestClient,
    db: Session,
    authed_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_audio_duration(monkeypatch, 120.0)
    track_id = _upload(client, "song.mp3")
    assert client.get(f"/api/v1/tracks/{track_id}/segments").json()["data"] == {"items": []}

    ids = []
    for start, name in ((40_000, "CHORUS"), (10_000, "VERSE")):
        res = client.post(
            f"/api/v1/tracks/{track_id}/segments",
            json={"start_ms": start, "end_ms": start + 5_000, "name": name},
        )
        ids.append(res.json()["data"]["segment_id"])

    items = client.get(f"/api/v1/tracks/{track_id}/segments").json()["data"]["items"]
    assert [(s["segment_id"], s["start_ms"], s["name"]) for s in items] == [
        (ids[1], 10_000, "VERSE"),
        (ids[0], 40_000, "CHORUS"),
    ]

    detail = client.get(f"/api/v1/segments/{ids[0]}").json()["data"]
    assert detail == items[1]
    assert detail["duration_ms"] == 5_000


def test_segment_of_other_workspace_is_not_found(
    client: TestClient, db: Session, authed_workspace: Workspace
) -> None:
    from app.domains.segment.models import Segment

    other = _other_workspace_track(db)
    seg = Segment(track_id=other.id, start_ms=0, end_ms=1000, version=1)
    db.add(seg)
    db.commit()
    for segment_id in (seg.id, 999999):
        res = client.get(f"/api/v1/segments/{segment_id}")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "SEGMENT_NOT_FOUND"


def test_lookup_requires_login(client: TestClient) -> None:
    for path in (
        "/api/v1/tracks",
        "/api/v1/tracks/1",
        "/api/v1/tracks/1/segments",
        "/api/v1/segments/1",
    ):
        assert client.get(path).status_code == 401, path
