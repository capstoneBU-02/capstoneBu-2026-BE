from typing import Any, Generic, TypeVar

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class Meta(BaseModel):
    trace_id: str | None = None


class ApiResponse(BaseModel, Generic[T]):
    """모든 API 응답 래퍼 (노션 '예외처리 & 공통 응답 규칙' 기준).

    성공: {"success": true, "data": {...}}
    실패: {"success": false, "error": {"code": "...", "message": "..."}}
    """

    success: bool
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta | None = None

    @model_serializer(mode="wrap")
    def _drop_empty(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """비어 있는 error·meta는 응답에서 뺀다. (response_model로 직렬화할 때도 적용)"""
        result: dict[str, Any] = handler(self)
        for key in ("error", "meta"):
            if result.get(key) is None:
                result.pop(key, None)
        return result

    @classmethod
    def ok(cls, data: T | None = None, trace_id: str | None = None) -> "ApiResponse[T]":
        return cls(success=True, data=data, meta=Meta(trace_id=trace_id) if trace_id else None)

    @classmethod
    def fail(
        cls,
        code: str,
        message: str,
        details: Any | None = None,
        trace_id: str | None = None,
    ) -> "ApiResponse[T]":
        return cls(
            success=False,
            error=ErrorBody(code=code, message=message, details=details),
            meta=Meta(trace_id=trace_id) if trace_id else None,
        )
