import logging
from enum import Enum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import ApiResponse

logger = logging.getLogger(__name__)


class ErrorCode(Enum):
    """에러 코드 중앙 관리. (HTTP 상태, 코드, 사용자 노출 메시지)

    규칙
    - 도메인별로 주석으로 구분해 그룹화
    - code는 UPPER_SNAKE_CASE, message는 사용자에게 보여줄 한국어 문장
    - 기존 코드로 표현 가능하면 새로 만들지 않음
    """

    # Common
    INVALID_INPUT = (400, "INVALID_INPUT", "잘못된 입력입니다.")
    UNAUTHORIZED = (401, "UNAUTHORIZED", "인증이 필요합니다.")
    FORBIDDEN = (403, "FORBIDDEN", "접근 권한이 없습니다.")
    RESOURCE_NOT_FOUND = (404, "RESOURCE_NOT_FOUND", "요청한 리소스를 찾을 수 없습니다.")
    DUPLICATE_REQUEST = (409, "DUPLICATE_REQUEST", "중복된 요청입니다.")
    RATE_LIMIT_EXCEEDED = (429, "RATE_LIMIT_EXCEEDED", "요청 한도를 초과했습니다.")
    INTERNAL_SERVER_ERROR = (500, "INTERNAL_SERVER_ERROR", "서버 오류가 발생했습니다.")

    # 도메인별 코드는 아래에 추가 (예: Workspace, Song, Section, Job)

    @property
    def status(self) -> int:
        return int(self.value[0])

    @property
    def code(self) -> str:
        return str(self.value[1])

    @property
    def message(self) -> str:
        return str(self.value[2])


class BusinessException(Exception):
    """예측 가능한 비즈니스 규칙 위반. Service 레이어에서 raise 한다."""

    def __init__(self, error_code: ErrorCode, detail: str | None = None) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(detail or error_code.message)


def _json(status: int, body: ApiResponse[None]) -> JSONResponse:
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True))


def _trace_id(request: Request) -> str | None:
    value = getattr(request.state, "trace_id", None)
    return str(value) if value else None


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessException)
    async def handle_business(request: Request, exc: BusinessException) -> JSONResponse:
        ec = exc.error_code
        logger.warning("BusinessException: code=%s, message=%s", ec.code, exc)
        return _json(
            ec.status, ApiResponse.fail(ec.code, exc.detail or ec.message, None, _trace_id(request))
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        ec = ErrorCode.INVALID_INPUT
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return _json(ec.status, ApiResponse.fail(ec.code, ec.message, details, _trace_id(request)))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.RESOURCE_NOT_FOUND,
        }
        ec = mapping.get(exc.status_code)
        code = ec.code if ec else f"HTTP_{exc.status_code}"
        message = ec.message if ec else str(exc.detail)
        return _json(exc.status_code, ApiResponse.fail(code, message, None, _trace_id(request)))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        ec = ErrorCode.INTERNAL_SERVER_ERROR
        return _json(ec.status, ApiResponse.fail(ec.code, ec.message, None, _trace_id(request)))
