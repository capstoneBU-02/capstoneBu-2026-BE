from datetime import datetime

from pydantic import BaseModel


class StemCreatedResponse(BaseModel):
    """POST /segments/{segment_id}/stems 응답. 202(새 작업) / 200(재사용)."""

    stem_id: int
    segment_id: int
    version: int
    start_ms: int
    end_ms: int
    status: str
    bypass_separation: bool
    is_reused: bool
    created_at: datetime


class StageView(BaseModel):
    stage: str
    status: str


class FailureView(BaseModel):
    stage: str | None
    error_code: str
    message: str
    retryable: bool
    retry_count: int
    max_retries: int
    retries_left: int


class StemResponse(BaseModel):
    """GET /stems/{stem_id} 응답. 작업 실패도 HTTP 200이고 failure로 알린다."""

    stem_id: int
    segment_id: int
    version: int
    start_ms: int
    end_ms: int
    status: str
    current_stage: str | None
    bypass_separation: bool
    playable: bool
    review_reason: str | None
    stages: list[StageView]
    failure: FailureView | None
    updated_at: datetime


class RetryResponse(BaseModel):
    stem_id: int
    status: str
    retry_from: str
    retry_count: int
    max_retries: int
    retries_left: int


class CancelResponse(BaseModel):
    stem_id: int
    status: str
