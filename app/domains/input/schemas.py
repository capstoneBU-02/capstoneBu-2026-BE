from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrackUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: int
    filename: str
    duration_ms: int
    media_type: str
    is_separated_stem: bool
    created_at: datetime


class InputSegmentCreateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "InputSegmentCreateRequest":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms는 start_ms보다 커야 합니다.")
        return self


class InputSegmentResponse(BaseModel):
    segment_id: int
    track_id: int
    name: str | None
    start_ms: int
    end_ms: int
    duration_ms: int
    version: int
    created_at: datetime


class InputStemCreateResponse(BaseModel):
    stem_id: int
    segment_id: int
    version: int
    status: str
    target_part: Literal["guitar"] = "guitar"
    bypass_separation: bool
    is_reused: bool
    created_at: datetime


class InputStemStatusResponse(BaseModel):
    stem_id: int
    segment_id: int
    version: int
    status: str
    bypass_separation: bool
    updated_at: datetime


class TabCreateRequest(BaseModel):
    priority: str = Field(default="solo", min_length=1, max_length=30)
    tuning_id: str = Field(default="e_standard", min_length=1, max_length=50)
    capo: int = Field(default=0)


class TabCreateResponse(BaseModel):
    tab_id: int
    segment_id: int
    version: int
    status: Literal["queued", "processing", "success", "needs_review", "failed"]
    is_reused: bool
    created_at: datetime
