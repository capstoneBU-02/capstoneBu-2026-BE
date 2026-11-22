from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TrackUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: int
    filename: str
    duration_ms: int
    media_type: str
    is_separated_stem: bool
    created_at: datetime


class TrackResponse(BaseModel):
    """GET /tracks/{track_id} 응답, 목록 항목과 같다."""

    track_id: int
    filename: str
    duration_ms: int
    media_type: str
    is_separated_stem: bool
    segment_count: int  # 이 곡에 만든 구간 수
    created_at: datetime


class TrackListResponse(BaseModel):
    """GET /tracks 응답. 최근에 올린 곡부터."""

    items: list[TrackResponse]
