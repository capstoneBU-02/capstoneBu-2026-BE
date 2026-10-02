"""Celery 앱. API 서버와 같은 코드베이스에서 실행 명령만 다르게 띄운다.

실행: celery -A app.core.celery_app worker --loglevel=info
"""

from celery import Celery

from app.core.config import get_settings
from app.domains import find_domain_modules

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
)
