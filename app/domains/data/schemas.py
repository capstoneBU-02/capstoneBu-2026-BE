from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domains.data.models import AccessType

TaskType = Literal["tab", "stem", "comparison"]


class WorkspaceTaskItem(BaseModel):
    """GET /api/v1/workspaces/tasks 목록 항목 (DATA-02)."""

    task_id: int
    task_type: TaskType = "tab"
    segment_id: int
    track_id: int
    parent_task_id: int | None = None
    version: int
    track_filename: str
    start_sec: float
    end_sec: float
    target_part: str
    priority: str
    status: str
    is_reused: bool = False
    created_at: datetime


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total_count: int


class StorageUsageMeta(BaseModel):
    usage_bytes: int
    storage_limit_exceeded: bool


class WorkspaceTaskListResponse(BaseModel):
    """GET /api/v1/workspaces/tasks 응답 (DATA-02)."""

    items: list[WorkspaceTaskItem]
    pagination: PaginationMeta
    storage: StorageUsageMeta


class TaskDetailResponse(BaseModel):
    """GET /api/v1/tasks/{task_id} 상세 응답 (DATA-02)."""

    task_id: int
    segment_id: int
    track_id: int
    stem_id: int
    tab_id: int
    version: int
    start_sec: float
    end_sec: float
    priority: str
    tuning_id: str
    capo: int
    status: str
    is_reused: bool = False
    created_at: datetime


class PresignedUrlRequest(BaseModel):
    """POST /api/v1/files/presigned-url 요청 (DATA-05)."""

    file_key: str = Field(min_length=1, max_length=512)
    access_type: AccessType
    expires_in_seconds: int = Field(default=600, ge=60, le=3600)


class PresignedUrlResponse(BaseModel):
    """POST /api/v1/files/presigned-url 응답 (DATA-05)."""

    file_key: str
    presigned_url: str
    expires_at: datetime