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
    """단계별 진행 표시용."""

    stage: str
    status: str


class FailureView(BaseModel):
    """실패 안내. failed일 때만 포함한다."""

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
    """POST /stems/{stem_id}/retry 응답."""

    stem_id: int
    status: str
    retry_from: str
    retry_count: int
    max_retries: int
    retries_left: int


class CancelResponse(BaseModel):
    """POST /stems/{stem_id}/cancel 응답."""

    stem_id: int
    status: str


class StemStatusView(BaseModel):
    """processing-status의 기타 음원 부분."""

    stem_id: int
    status: str
    current_stage: str | None
    playable: bool
    retryable: bool  # 실패했고 다시 시도할 수 있음 (재시도 불가 실패·한도 초과 제외)


class TabStatusView(BaseModel):
    """processing-status의 타브 부분."""

    tab_id: int
    status: str
    current_stage: str | None
    retryable: bool


class ProcessingStatusResponse(BaseModel):
    """GET /segments/{segment_id}/processing-status 응답. 요청하지 않은 작업은 null."""

    segment_id: int
    stem: StemStatusView | None
    tab: TabStatusView | None
    updated_at: datetime | None  # stem·tab 중 늦은 변경 시각. 둘 다 없으면 null
