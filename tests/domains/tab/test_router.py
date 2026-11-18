from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.processing import sources
from app.domains.processing.models import Stem
from app.domains.tab import service
from app.main import create_app
from tests.domains.conftest import EnqueueSpy

WS = 10


def test_requires_session(client: TestClient) -> None:
    res = client.post("/api/v1/segments/1/tabs")
    assert res.status_code == 401


@pytest.fixture
def api(
    session_factory: Callable[[], Session],
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


def test_conditions(api: TestClient) -> None:
    res = api.get("/api/v1/tabs/conditions")

    assert res.status_code == 200
    assert res.json()["data"]["default"] == {
        "result_type": "original",
        "tuning_id": "e_standard",
        "capo": 0,
    }


def test_request_without_body_202_then_409(
    api: TestClient, ready_stem: Callable[..., Stem]
) -> None:
    ready_stem()

    first = api.post("/api/v1/segments/1/tabs")
    assert first.status_code == 202
    data = first.json()["data"]
    assert (data["version"], data["status"], data["is_reused"]) == (1, "queued", False)

    second = api.post("/api/v1/segments/1/tabs", json={"capo": 0})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TAB_IN_PROGRESS"


def test_unsupported_condition_is_422(api: TestClient, ready_stem: Callable[..., Stem]) -> None:
    ready_stem()

    res = api.post("/api/v1/segments/1/tabs", json={"tuning_id": "drop_d"})

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "UNSUPPORTED_CONDITION"


def test_get_list_and_not_ready_result(api: TestClient, ready_stem: Callable[..., Stem]) -> None:
    ready_stem()
    tab_id = api.post("/api/v1/segments/1/tabs").json()["data"]["tab_id"]

    status = api.get(f"/api/v1/tabs/{tab_id}")
    assert status.status_code == 200
    assert status.json()["data"]["status"] == "queued"

    items = api.get("/api/v1/segments/1/tabs").json()["data"]["items"]
    assert [i["tab_id"] for i in items] == [tab_id]

    result = api.get(f"/api/v1/tabs/{tab_id}/result")
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "TAB_NOT_READY"


def test_missing_tab_is_404(api: TestClient) -> None:
    res = api.get("/api/v1/tabs/999")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "TAB_NOT_FOUND"


def test_download_unknown_format_is_422(api: TestClient) -> None:
    res = api.get("/api/v1/tabs/1/download?format=pdf")

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "UNSUPPORTED_DOWNLOAD_FORMAT"
