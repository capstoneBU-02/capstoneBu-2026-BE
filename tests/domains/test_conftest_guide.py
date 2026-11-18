"""테스트 DB 준비 실패를 종류별로 다룬다 (이슈 #57).

Postgres가 꺼져 있을 때만 건너뛰고, 켜져 있는데 권한·접속 문제가 있으면
해결 방법을 보여 주고 멈춘다.
"""

import psycopg
import psycopg.errors as pg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from tests.domains import conftest

URL = make_url("postgresql+psycopg://dev-user:pw@localhost:5432/capstone_test")


def _fail_with(monkeypatch: pytest.MonkeyPatch, exc: DBAPIError) -> None:
    def _raise(_url: object) -> None:
        raise exc

    monkeypatch.setattr(conftest, "_server_reachable", lambda _url: True)
    monkeypatch.setattr(conftest, "_ensure_database", _raise)


def test_no_permission_exits_with_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = pg.InsufficientPrivilege("permission denied to create database")
    _fail_with(monkeypatch, ProgrammingError("CREATE DATABASE", {}, orig))
    with pytest.raises(pytest.exit.Exception) as e:
        conftest._prepare_test_database(URL)
    msg = str(e.value)
    assert 'ALTER ROLE "dev-user" CREATEDB' in msg  # 따옴표로 감싸 그대로 실행할 수 있게
    assert "permission denied to create database" in msg


def test_connect_failure_with_server_up_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버는 켜져 있는데 접속이 안 되면(비밀번호 등) 건너뛰지 않는다. 건너뛰면 통과처럼 보인다."""
    orig = psycopg.OperationalError("FATAL: password authentication failed")
    _fail_with(monkeypatch, OperationalError("connect", {}, orig))
    with pytest.raises(pytest.exit.Exception) as e:
        conftest._prepare_test_database(URL)
    assert "접속하지 못해" in str(e.value)


def test_server_down_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "_server_reachable", lambda _url: False)
    with pytest.raises(pytest.skip.Exception):
        conftest._prepare_test_database(URL)


def test_closed_port_is_not_reachable_quickly() -> None:
    """닫힌 포트는 몇 초 안에 '꺼짐'으로 판단한다 (Windows는 접속 시간 초과까지 오래 걸림)."""
    closed = make_url("postgresql+psycopg://u@127.0.0.1:1/x_test")
    assert conftest._server_reachable(closed, timeout=1.0) is False


def test_other_errors_are_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, ProgrammingError("x", {}, pg.SyntaxError("syntax error")))
    with pytest.raises(ProgrammingError):
        conftest._prepare_test_database(URL)


def test_empty_error_message() -> None:
    exc = ProgrammingError("x", {}, pg.InsufficientPrivilege(""))
    assert conftest._first_line(exc) == "InsufficientPrivilege"
