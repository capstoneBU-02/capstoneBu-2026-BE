from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.compare import service
from app.domains.processing import sources as stem_sources
from app.domains.processing.models import Stem
from app.main import create_app
from tests.domains.compare.conftest import WS
from tests.domains.conftest import EnqueueSpy


def test_requires_session(client: TestClient) -> None:
    """ACC 세션 연결 전에는 401 (임시 의존성)."""
    assert client.post("/api/v1/performances/1/comparisons").status_code == 401
    assert client.get("/api/v1/comparisons/1").status_code == 401


@pytest.fixture
def api(
    session_factory: Callable[[], Session],
    ready: Stem,
    enqueue: EnqueueSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    app = create_app()

    def _db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[stem_sources.get_current_workspace_id] = lambda: WS
    monkeypatch.setattr(service, "_default_enqueue", enqueue)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_request_202_then_409_and_status(api: TestClient) -> None:
    first = api.post("/api/v1/performances/500/comparisons")
    assert first.status_code == 202
    data = first.json()["data"]
    assert (data["status"], data["is_reused"], data["segment_id"]) == ("queued", False, 1)

    dup = api.post("/api/v1/performances/500/comparisons")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "COMPARISON_IN_PROGRESS"

    status = api.get(f"/api/v1/comparisons/{data['comparison_id']}").json()["data"]
    assert [s["stage"] for s in status["stages"]] == ["align", "transcription", "compare"]
    assert status["failure"] is None


def test_result_before_done_is_409(api: TestClient) -> None:
    cid = api.post("/api/v1/performances/500/comparisons").json()["data"]["comparison_id"]
    res = api.get(f"/api/v1/comparisons/{cid}/result")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "COMPARISON_NOT_READY"


def test_unknown_comparison_and_region_404(api: TestClient) -> None:
    assert api.get("/api/v1/comparisons/999").json()["error"]["code"] == "COMPARISON_NOT_FOUND"
    cid = api.post("/api/v1/performances/500/comparisons").json()["data"]["comparison_id"]
    res = api.patch(f"/api/v1/comparisons/{cid}/regions/999", json={"is_checked": True})
    assert res.status_code == 404 and res.json()["error"]["code"] == "REGION_NOT_FOUND"
