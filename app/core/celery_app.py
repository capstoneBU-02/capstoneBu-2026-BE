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
    backend=settings.redis_url,
    include=find_domain_modules("tasks"),  # 각 도메인의 tasks.py 자동 등록
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Seoul",
    task_track_started=True,
    task_acks_late=True,  # 워커가 죽으면 작업을 다시 큐로
    worker_prefetch_multiplier=1,  # GPU 작업은 한 번에 하나씩
    # 큐: cpu(기본) / gpu(기타 분리). 태스크는 @celery_app.task(queue="gpu")로 지정
    # PoC 초기에는 worker 하나가 두 큐를 함께 처리한다 (docker-compose.yml)
    task_default_queue="cpu",
)


@worker_process_init.connect
def preload_ai_models(**_: object) -> None:
    """워커 프로세스 시작 시 AI 모델 사전 로드 (첫 작업 지연 방지).

    api·CI에는 AI 패키지가 없으므로 모듈 최상단 import 금지. 여기서도 함수 안에서 지연 import 한다.
    예) from app.domains.<기능>.models_loader import preload; preload(settings)
    """
    logger.info(
        "AI 모델 사전 로드 (config_version=%s, cache_dir=%s) — 등록된 로더 없음",
        settings.ai_config_version,
        settings.ai_model_cache_dir,
    )
