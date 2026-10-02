import os

# 테스트는 외부 서비스(DB·Redis·저장소) 없이 돌아가야 한다.
# .env의 APP_ENV=local이면 앱 시작 시 버킷 생성을 시도하므로, app import 전에 덮어쓴다.
os.environ["APP_ENV"] = "test"

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
