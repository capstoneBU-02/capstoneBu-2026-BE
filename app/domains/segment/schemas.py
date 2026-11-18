from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# SEG 명세에 정의된 구성 이름 7개
SegmentName = Literal[
    "INTRO",
    "VERSE",
    "PRE_CHORUS",
    "CHORUS",
    "BRIDGE",
    "INTERLUDE",
    "OUTRO",
]


class SegmentCreateRequest(BaseModel):
    """SEG 구간 생성 요청."""

    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(ge=0, strict=True)


class SegmentUpdateRequest(BaseModel):
    """SEG 구간 수정 요청."""

    start_ms: int | None = Field(default=None, ge=0, strict=True)
    end_ms: int | None = Field(default=None, ge=0, strict=True)
    name: SegmentName | None = None

    @model_validator(mode="after")
    def validate_update_fields(self) -> Self:
        """수정 요청의 필드 조합을 검증한다."""

        fields = self.model_fields_set

        if not fields:
            raise ValueError("수정할 필드를 하나 이상 입력해야 합니다.")

        has_start = "start_ms" in fields
        has_end = "end_ms" in fields

        if has_start != has_end:
            raise ValueError("start_ms와 end_ms는 함께 전달해야 합니다.")

        if has_start and (self.start_ms is None or self.end_ms is None):
            raise ValueError("구간 시작 및 종료 시간은 null일 수 없습니다.")

        if "name" in fields and self.name is None:
            raise ValueError("구성 이름은 null일 수 없습니다.")

        return self


class SegmentResponse(BaseModel):
    """SEG 구간 생성 및 수정 응답."""

    model_config = ConfigDict(from_attributes=True)

    segment_id: int
    track_id: int
    start_ms: int
    end_ms: int
    duration_ms: int
    name: str | None
    version: int
    created_at: datetime
    updated_at: datetime
