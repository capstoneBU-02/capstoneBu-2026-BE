from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.exceptions import BusinessException, ErrorCode, register_exception_handlers
from app.core.middleware import TraceIdMiddleware


class Body(BaseModel):
    name: str


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    @app.get("/biz")
    def biz() -> None:
        raise BusinessException(ErrorCode.RESOURCE_NOT_FOUND)

    @app.post("/validate")
    def validate(body: Body) -> None:
        return None

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("unexpected")

    return app


client = TestClient(_app(), raise_server_exceptions=False)


def test_business_exception_format() -> None:
    res = client.get("/biz")
    body = res.json()

    assert res.status_code == 404
    assert body["success"] is False
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert body["meta"]["trace_id"]


def test_validation_error_format() -> None:
    res = client.post("/validate", json={})
    body = res.json()

    assert res.status_code == 400
    assert body["error"]["code"] == "INVALID_INPUT"
    assert body["error"]["details"][0]["field"] == "name"


def test_unknown_route_returns_not_found() -> None:
    res = client.get("/nope")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_unexpected_exception_hides_detail() -> None:
    res = client.get("/boom")
    body = res.json()

    assert res.status_code == 500
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "unexpected" not in res.text
