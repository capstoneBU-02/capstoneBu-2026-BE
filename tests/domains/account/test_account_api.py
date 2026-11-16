from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.account.models import TesterCode, Workspace
from app.domains.account.service import hash_token
from app.main import app


@pytest.fixture
def client(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def tester_code(db: Session) -> tuple[Workspace, str]:
    workspace = Workspace(owner_type="TESTER_CODE")
    db.add(workspace)
    db.flush()

    raw_code = "TESTER-2026-ABCD"
    code = TesterCode(
        code_hash=hash_token(raw_code),
        workspace_id=workspace.id,
        status="ACTIVE",
    )
    db.add(code)
    db.commit()
    return workspace, raw_code


def test_tester_login_success_and_sets_cookie(
    client: TestClient, tester_code: tuple[Workspace, str]
) -> None:
    workspace, raw_code = tester_code

    res = client.post("/api/v1/auth/tester-login", json={"tester_code": raw_code})

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"]["workspace_id"] == workspace.id
    assert body["data"]["is_reconnected"] is False
    assert "ws_token" in res.cookies


def test_tester_login_reconnected(client: TestClient, tester_code: tuple[Workspace, str]) -> None:
    _, raw_code = tester_code

    first = client.post("/api/v1/auth/tester-login", json={"tester_code": raw_code})
    assert first.status_code == 200
    assert first.json()["data"]["is_reconnected"] is False

    second = client.post("/api/v1/auth/tester-login", json={"tester_code": raw_code})
    assert second.status_code == 200
    assert second.json()["data"]["is_reconnected"] is True


def test_tester_login_invalid_format_returns_400(client: TestClient) -> None:
    res = client.post("/api/v1/auth/tester-login", json={"tester_code": "INVALID-1234"})

    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_CODE_FORMAT"


def test_tester_login_unknown_code_returns_401(client: TestClient) -> None:
    res = client.post("/api/v1/auth/tester-login", json={"tester_code": "TESTER-NONE-9999"})

    assert res.status_code == 401
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TESTER_CODE_NOT_FOUND"


def test_get_my_workspace_authenticated_and_unauthenticated(
    client: TestClient, tester_code: tuple[Workspace, str]
) -> None:
    # 1. 쿠키 없이 호출 시 401
    unauth_res = client.get("/api/v1/workspaces/me")
    assert unauth_res.status_code == 401
    assert unauth_res.json()["error"]["code"] == "UNAUTHORIZED_SESSION"

    # 2. 로그인 후 자동 저장된 쿠키로 호출 시 200
    workspace, raw_code = tester_code
    login_res = client.post("/api/v1/auth/tester-login", json={"tester_code": raw_code})
    assert login_res.status_code == 200

    me_res = client.get("/api/v1/workspaces/me")
    assert me_res.status_code == 200
    body = me_res.json()
    assert body["success"] is True
    assert body["data"]["workspace_id"] == workspace.id
    assert body["data"]["session_active"] is True
