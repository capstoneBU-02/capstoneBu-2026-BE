from typing import Any

import redis
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.response import ApiResponse
from app.core.storage import get_s3

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=ApiResponse[dict[str, str]])
def liveness() -> ApiResponse[dict[str, str]]:
    """서버가 떠 있는지만 확인."""
    return ApiResponse.ok({"status": "ok"})


@router.get("/ready", response_model=ApiResponse[dict[str, Any]])
def readiness(response: Response, db: Session = Depends(get_db)) -> ApiResponse[dict[str, Any]]:
    """DB · Redis · 파일 저장소 연결 상태 확인. 하나라도 실패하면 503 (배포 헬스 게이트용)."""
    settings = get_settings()
    checks: dict[str, Any] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e.__class__.__name__}"

    try:
        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e.__class__.__name__}"

    try:
        get_s3().head_bucket(Bucket=settings.s3_bucket)
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {e.__class__.__name__}"

    if any(v != "ok" for v in checks.values()):
        response.status_code = 503
    return ApiResponse.ok(checks)
