from datetime import datetime

from pydantic import BaseModel


class ComparisonCreatedResponse(BaseModel):
    """POST /performances/{performance_id}/comparisons 응답. 202(새 작업) / 200(재사용)."""

    comparison_id: int
    performance_id: int
    segment_id: int
    status: str
    is_reused: bool
    created_at: datetime


class StageView(BaseModel):
    """단계별 진행 표시용."""

    stage: str
    status: str


class FailureView(BaseModel):
    """실패 안내. failed일 때만 포함한다 (JOB·TAB과 같은 형식)."""

    stage: str | None
    error_code: str
    message: str
    retryable: bool
    retry_count: int
    max_retries: int
    retries_left: int


class ComparisonResponse(BaseModel):
    """GET /comparisons/{comparison_id} 응답. 작업 실패도 HTTP 200이고 failure로 알린다."""

    comparison_id: int
    status: str
    current_stage: str | None
    not_comparable_reason: str | None
    stages: list[StageView]
    failure: FailureView | None
    updated_at: datetime


class SegmentView(BaseModel):
    """비교한 구간."""

    segment_id: int
    name: str | None
    start_ms: int
    end_ms: int


class BaselineView(BaseModel):
    """비교 기준 (CMP-01). 원곡이 없으면 source=separated_input."""

    source: str
    stem_id: int
    tab_id: int | None
    segment: SegmentView


class OverallTempoView(BaseModel):
    """전체 빠르기. 전체적으로 느린 것은 개별 타이밍 차이로 세지 않는다 (CMP-02)."""

    is_slow: bool
    slope: float


class SummaryView(BaseModel):
    """구간 개수 요약."""

    difference_count: int
    unavailable_count: int


class RegionView(BaseModel):
    """다시 들어 볼 구간. 시간은 구간 시작 기준 밀리초."""

    region_id: int
    type: str
    start_ms: int
    end_ms: int
    performance_start_ms: int
    performance_end_ms: int
    reason: str | None
    is_checked: bool


class ComparisonResultResponse(BaseModel):
    """GET /comparisons/{comparison_id}/result 응답. 정답 판정이 아니므로 항상 초안이다."""

    comparison_id: int
    status: str
    is_draft: bool
    not_comparable_reason: str | None
    baseline: BaselineView
    overall_tempo: OverallTempoView | None
    summary: SummaryView
    regions: list[RegionView]
    config_version: str


class PlaybackTrackView(BaseModel):
    """재생 대상 하나. available이 false면 url은 null."""

    type: str  # original / stem / performance
    label: str
    available: bool
    url: str | None
    offset_ms: int  # 파일 안에서 구간이 시작되는 위치


class PlaybackResponse(BaseModel):
    """GET /comparisons/{comparison_id}/playback 응답 (CMP-04)."""

    comparison_id: int
    is_partial: bool
    tracks: list[PlaybackTrackView]
    tab_id: int | None
    expires_at: datetime


class RegionCheckRequest(BaseModel):
    """PATCH /comparisons/{comparison_id}/regions/{region_id} 요청."""

    is_checked: bool


class RegionCheckResponse(BaseModel):
    """확인 표시 결과."""

    region_id: int
    is_checked: bool


class RetryResponse(BaseModel):
    """POST /comparisons/{comparison_id}/retry 응답."""

    comparison_id: int
    status: str
    retry_from: str
    retry_count: int
    max_retries: int
    retries_left: int
