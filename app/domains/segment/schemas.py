from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SegmentCreateRequest(BaseModel):
    """SEG 구간 생성 요청."""

    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_range(self) -> "SegmentCreateRequest":
        if self.start_ms >= self.end_ms:
            raise ValueError("시작 시간은 종료 시간보다 작아야 합니다.")
        return self


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
