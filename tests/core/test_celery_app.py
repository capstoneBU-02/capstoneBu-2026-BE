"""Celery 등록 설정: Redis가 끊겨 있어도 작업 요청이 오래 멈추지 않는다 (이슈 #44)."""

import os
import subprocess
import sys
import textwrap
import time

from app.core.celery_app import celery_app
from app.domains.processing.tasks import preprocess


def test_no_result_backend() -> None:
    """작업 상태는 DB에 있다. 결과 저장소가 있으면 등록 때마다 구독해서 Redis 장애 때 멈춘다."""
    assert celery_app.conf.result_backend is None
    assert celery_app.conf.task_ignore_result is True
    assert preprocess.ignore_result is True


def test_api_enqueue_fails_fast_when_redis_is_down() -> None:
    """API가 실제로 쓰는 등록 경로(enqueue_pipeline)로, 아무도 듣지 않는 Redis에 등록한다.

    설정은 import 시점에 읽으므로 새 프로세스에서 REDIS_URL만 바꿔 실행한다.
    이전에는 결과 저장소 재연결 때문에 1분 넘게 멈췄다.
    """
    code = textwrap.dedent(
        """
        import logging
        logging.disable(logging.CRITICAL)
        from kombu.exceptions import OperationalError
        from app.domains.processing.models import StemStage
        from app.domains.processing.tasks import enqueue_pipeline
        try:
            enqueue_pipeline(1, StemStage.PREPROCESS)
        except OperationalError:
            print("OperationalError")
        """
    )
    env = {**os.environ, "REDIS_URL": "redis://127.0.0.1:6390/0", "PYTHONPATH": os.getcwd()}
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60
    )
    elapsed = time.monotonic() - started
    assert "OperationalError" in proc.stdout, proc.stderr[-2000:]
    assert elapsed < 12  # 기본값 최악 약 10초 (+ 프로세스 시작)
