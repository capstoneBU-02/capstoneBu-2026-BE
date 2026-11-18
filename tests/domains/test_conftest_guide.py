"""테스트 DB를 만들 권한이 없을 때 해결 방법을 보여 주고 멈춘다 (이슈 #57)."""

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from tests.domains import conftest

URL = make_url("postgresql+psycopg://capstone:pw@localhost:5432/capstone_test")


def _raise(exc: Exception):  # type: ignore[no-untyped-def]
    def _f(_url: object) -> None:
        raise exc

    return _f


def test_no_permission_exits_with_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    err = ProgrammingError("CREATE DATABASE", {}, Exception("permission denied to create database"))
    monkeypatch.setattr(conftest, "_ensure_database", _raise(err))
    with pytest.raises(pytest.exit.Exception) as e:
        conftest._prepare_test_database(URL)
    msg = str(e.value)
    assert "capstone_test" in msg and "ALTER ROLE capstone CREATEDB" in msg
    assert "permission denied" in msg
    assert ":pw@" not in msg  # 접속 주소(비밀번호 포함)는 보여 주지 않는다


def test_no_postgres_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    err = OperationalError("connect", {}, Exception("connection refused"))
    monkeypatch.setattr(conftest, "_ensure_database", _raise(err))
    with pytest.raises(pytest.skip.Exception):
        conftest._prepare_test_database(URL)
