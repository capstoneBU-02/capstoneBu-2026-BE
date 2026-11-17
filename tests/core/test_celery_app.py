"""Celery 등록 설정: Redis가 끊겨 있어도 작업 요청이 오래 멈추지 않는다 (이슈 #44)."""

import time

import pytest

from app.core.celery_app import celery_app
from app.domains.processing.tasks import preprocess


def test_results_are_not_stored() -> None:
    """작업 상태는 DB에 있다. 결과를 저장하면 등록 때마다 결과 저장소에 구독한다."""
    assert celery_app.conf.task_ignore_result is True
    assert preprocess.ignore_result is True


def test_enqueue_fails_fast_when_redis_is_down() -> None:
    """아무도 듣지 않는 Redis 주소로 등록하면 몇 초 안에 예외가 난다 (이전에는 1분 넘게 멈춤)."""
    with celery_app.connection_for_write("redis://127.0.0.1:6390/0") as conn:
        started = time.monotonic()
        with pytest.raises(Exception):  # noqa: B017 - kombu·redis 예외 종류는 환경마다 다르다
            preprocess.si(1).apply_async(connection=conn)
        assert time.monotonic() - started < 15
