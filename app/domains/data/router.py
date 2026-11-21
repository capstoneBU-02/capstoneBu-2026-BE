from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.data import service
from app.domains.data.schemas import (
    DataTabDownloadResponse,
    PresignedUrlRequest,
    PresignedUrlResponse,
    TaskDetailResponse,
    WorkspaceTaskListResponse,
)
from app.domains.processing.sources import get_current_workspace_id

router = APIRouter(tags=["data"])


@router.get(
    "/tabs/{tab_id}/download",
    response_model=ApiResponse[DataTabDownloadResponse],
)
def download_tab(
    tab_id: int,
    file_format: str = Query("musicxml", alias="format"),
    page_option: str = Query("all"),
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[DataTabDownloadResponse]:
    """DATA-01: 생성 완료된 타브 악보의 보안 다운로드 링크를 발급한다."""
    data = service.get_tab_download(db, workspace_id, tab_id, file_format, page_option)
    return ApiResponse.ok(data)


@router.get(
    "/workspaces/tasks",
    response_model=ApiResponse[WorkspaceTaskListResponse],
)
def list_workspace_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[WorkspaceTaskListResponse]:
    """DATA-02: 현재 워크스페이스에 보관된 이전 작업 내역을 조회한다."""
    data = service.list_workspace_tasks(db, workspace_id, page, limit)
    return ApiResponse.ok(data)


@router.get(
    "/tasks/{task_id}",
    response_model=ApiResponse[TaskDetailResponse],
)
def get_task_detail(
    task_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TaskDetailResponse]:
    """DATA-02: 특정 이전 작업의 세부 결과 및 관련 자원 ID를 재열람한다."""
    data = service.get_task_detail(db, workspace_id, task_id)
    return ApiResponse.ok(data)


@router.post(
    "/files/presigned-url",
    response_model=ApiResponse[PresignedUrlResponse],
)
def create_presigned_url(
    body: PresignedUrlRequest,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[PresignedUrlResponse]:
    """DATA-05: 비공개 파일 접근용 임시 Presigned URL을 발급한다."""
    data = service.create_presigned_file_url(db, workspace_id, body)
    return ApiResponse.ok(data)