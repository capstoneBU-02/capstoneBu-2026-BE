from app.core.celery_app import celery_app
from app.domains.health.tasks import ping


def test_ping_task_eager() -> None:
    celery_app.conf.task_always_eager = True
    try:
        assert ping.delay().get(timeout=5) == "pong"
    finally:
        celery_app.conf.task_always_eager = False
