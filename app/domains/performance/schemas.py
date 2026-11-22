from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

PerformanceSource = Literal["RECORDING", "UPLOAD"]


class PerformanceCreateResponse(BaseModel):
    """연주 녹음 및 파일 업로드 저장 응답."""

    model_config = ConfigDict(from_attributes=True)

    performance_id: int
    workspace_id: int
    track_id: int
    segment_id: int
    source: PerformanceSource
    start_ms: int
    end_ms: int
    created_at: datetime


class PerformanceListItem(BaseModel):
    """구간별 연주 목록 항목."""

    model_config = ConfigDict(from_attributes=True)

    performance_id: int
    track_id: int
    source: PerformanceSource
    start_ms: int
    end_ms: int
    created_at: datetime


class PerformanceListResponse(BaseModel):
    """특정 구간에 등록된 연주 목록 응답."""

    segment_id: int
    performances: list[PerformanceListItem]
