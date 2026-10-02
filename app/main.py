import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.core.storage import ensure_bucket
from app.domains.health.router import router as health_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.app_env == "local":
        try:
            ensure_bucket()
        except Exception:
            logger.warning("버킷 생성 실패 - 파일 저장소가 아직 준비되지 않았을 수 있습니다.")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Capstone Backend",
        version="0.1.0",
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,  # 워크스페이스 세션 쿠키 사용
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    # 라우터 등록 — 새 도메인은 여기 추가
    app.include_router(health_router, prefix=settings.api_prefix)

    return app


app = create_app()
