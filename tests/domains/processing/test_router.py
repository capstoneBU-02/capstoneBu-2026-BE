from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.processing import service, sources
from app.domains.processing.models import StemStatus
from app.main import create_app
from tests.domains.processing.conftest import EnqueueSpy, FakeSegments

WS = 10


def test_requires_session(client: TestClient) -> None:
    """ACC 세션 연결 전에는 401 (임시 의존성)."""
    res = client.post("/api/v1/segments/1/stems")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.fixture
def api(
    session_factory: Callable[[], Session],
    segments: FakeSegments,
    enqueue: EnqueueSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    app = create_app()

    def _db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[sources.get_current_workspace_id] = lambda: WS
    monkeypatch.setattr(service, "_default_enqueue", enqueue)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_request_202_then_409(api: TestClient, segments: FakeSegments) -> None:
    segments.add(segment_id=1)
    first = api.post("/api/v1/segments/1/stems")
    assert first.status_code == 202
    body = first.json()
    assert body["success"] is True
    data = body["data"]
    assert (data["version"], data["status"], data["is_reused"]) == (1, "queued", False)
    assert (data["start_ms"], data["end_ms"]) == (1000, 5000)

    second = api.post("/api/v1/segments/1/stems")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "STEM_IN_PROGRESS"


def test_reuse_returns_200(
    api: TestClient, segments: FakeSegments, session_factory: Callable[[], Session]
) -> None:
    segments.add(segment_id=1)
    stem_id = api.post("/api/v1/segments/1/stems").json()["data"]["stem_id"]
    with session_factory() as db:
        stem = service.get_owned_stem(db, WS, stem_id)
        stem.status = StemStatus.SUCCESS
        stem.storage_key = "guitar.wav"
        db.commit()

    res = api.post("/api/v1/segments/1/stems")
    assert res.status_code == 200 and res.json()["data"]["is_reused"] is True

    got = api.get(f"/api/v1/stems/{stem_id}").json()["data"]
    assert got["playable"] is True and got["failure"] is None


def test_unknown_stem_404(api: TestClient) -> None:
    res = api.get("/api/v1/stems/999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "STEM_NOT_FOUND"
