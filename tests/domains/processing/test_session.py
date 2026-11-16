"""실제 로그인 쿠키로 JOB·CMP API를 호출할 때 세션의 워크스페이스로 처리되는지 확인한다."""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.account.models import TesterCode
from app.domains.account.service import hash_token
from app.domains.processing import service
from app.main import create_app
from tests.domains.conftest import EnqueueSpy, FakeSegments

WS = 10
CODE = "TESTER-SESSION-0001"


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

    app.dependency_overrides[get_db] = _db  # 세션 의존성은 바꾸지 않는다
    monkeypatch.setattr(service, "_default_enqueue", enqueue)
    with session_factory() as db:
        db.add(TesterCode(code_hash=hash_token(CODE), workspace_id=WS, status="ACTIVE"))
        db.commit()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_stem_api_uses_session_workspace(api: TestClient, segments: FakeSegments) -> None:
    segments.add(segment_id=1)  # WS 소유 구간
    segments.add(segment_id=8, track_id=200, workspace_id=99)  # 다른 워크스페이스

    assert api.post("/api/v1/segments/1/stems").status_code == 401

    login = api.post("/api/v1/auth/tester-login", json={"tester_code": CODE})
    assert login.status_code == 200 and login.json()["data"]["workspace_id"] == WS

    created = api.post("/api/v1/segments/1/stems")
    assert created.status_code == 202
    stem_id = created.json()["data"]["stem_id"]
    assert api.get(f"/api/v1/stems/{stem_id}").status_code == 200

    other = api.post("/api/v1/segments/8/stems")
    assert other.status_code == 404


def test_comparison_api_requires_session(api: TestClient) -> None:
    res = api.get("/api/v1/comparisons/1")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED_SESSION"
