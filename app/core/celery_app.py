"""Celery 앱. API 서버와 같은 코드베이스에서 실행 명령만 다르게 띄운다.

실행: celery -A app.core.celery_app worker --loglevel=info
"""

import logging

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import get_settings
from app.domains import find_domain_modules

logger = logging.getLogger(__name__)

settings = get_settings()

celery_app = Celery(
    "capstone",
    broker=settings.redis_url,
    # 결과 저장소는 두지 않는다. 작업 상태는 DB(stems·comparisons·tabs)에 저장하고 Celery 결과는
    # 읽지 않는다. 결과 저장소가 있으면 등록 때마다 구독해서 Redis가 끊기면 1분 넘게 멈춘다 (#44)
    backend=None,
    include=find_domain_modules("tasks"),  # 각 도메인의 tasks.py 자동 등록
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Seoul",
    task_acks_late=True,  # 워커가 죽으면 작업을 다시 큐로
    worker_prefetch_multiplier=1,  # GPU 작업은 한 번에 하나씩
    # 큐: cpu(기본) / gpu(기타 분리). 태스크는 @celery_app.task(queue="gpu")로 지정
    # PoC 초기에는 worker 하나가 두 큐를 함께 처리한다 (docker-compose.yml)
    task_default_queue="cpu",
    task_ignore_result=True,
    # 브로커 연결·등록 재시도를 짧게 제한한다 (Redis 장애 시 요청이 오래 멈추지 않게).
    # 최악의 경우 (재시도 + 1) × 연결 제한 + 재시도 간격 합. 기본값이면 3 × 3초 + 0.5초 ≈ 10초.
    # 읽기 제한(socket_timeout)은 두지 않는다: Redis가 잠깐 느려질 때 등록 실패로 바꾸지 않게
    broker_connection_timeout=settings.celery_connect_timeout_sec,
    broker_transport_options={"socket_connect_timeout": settings.celery_connect_timeout_sec},
    task_publish_retry_policy={
        "max_retries": settings.celery_publish_max_retries,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 1,
    },
)


@worker_process_init.connect
def preload_ai_models(**_: object) -> None:
    """워커 프로세스 시작 시 AI 모델 사전 로드 (첫 작업 지연 방지).

    api·CI에는 AI 패키지가 없으므로 모듈 최상단 import 금지. 여기서도 함수 안에서 지연 import 한다.
    예) from app.domains.<기능>.models_loader import preload; preload(settings)
    """
    logger.info(
        "AI 모델 사전 로드 (config_version=%s, cache_dir=%s)",
        settings.ai_config_version,
        settings.ai_model_cache_dir,
    )
    try:
        from app.domains.tab.pipeline.transcription import preload

        preload(settings)
    except Exception:
        # 사전 로드는 첫 작업을 빠르게 하려는 것뿐이다. 실패해도 작업에서 다시 로드한다
        logger.exception("음 인식 모델 사전 로드 실패")
