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
