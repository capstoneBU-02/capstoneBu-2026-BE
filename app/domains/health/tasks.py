"""워커 연결 확인용 작업.

도메인 작업은 각 도메인의 tasks.py에 둔다 (예: app/domains/jobs/tasks.py → 분리·전사 단계별 작업).
"""

from app.core.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> str:
    """워커 연결 확인용 작업."""
    return "pong"
