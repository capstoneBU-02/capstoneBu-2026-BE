from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SegmentCreateRequest(BaseModel):
    """SEG 구간 생성 요청."""

    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(ge=0, strict=True)


class SegmentResponse(BaseModel):
    """SEG 구간 생성 응답."""

    model_config = ConfigDict(from_attributes=True)

    segment_id: int
    track_id: int
    start_ms: int
    end_ms: int
    duration_ms: int
    name: str | None
    version: int
    created_at: datetime
