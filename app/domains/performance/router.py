from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.account.deps import get_current_workspace
from app.domains.account.models import Workspace
from app.domains.performance import service
from app.domains.performance.schemas import (
    PerformanceCreateResponse,
    PerformanceListResponse,
    PerformanceSource,
)

router = APIRouter(tags=["performance"])


@router.post(
    "/segments/{segment_id}/performances",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[PerformanceCreateResponse],
)
def create_performance(
    segment_id: int,
    audio_file: Annotated[UploadFile, File(...)],
    source: Annotated[PerformanceSource, Form()],
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ApiResponse[PerformanceCreateResponse]:
    """연주 녹음 또는 업로드 파일을 저장하고 이전 연주와 연결한다."""
    result = service.create_performance(
        db=db,
        workspace_id=workspace.id,
        segment_id=segment_id,
        source=source,
        audio_file=audio_file,
    )
    return ApiResponse.ok(result)


@router.get(
    "/segments/{segment_id}/performances",
    response_model=ApiResponse[PerformanceListResponse],
)
def get_performances(
    segment_id: int,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ApiResponse[PerformanceListResponse]:
    """특정 구간에 등록된 연주 목록을 최신순으로 조회한다."""
    result = service.get_performances_by_segment(
        db=db,
        workspace_id=workspace.id,
        segment_id=segment_id,
    )
    return ApiResponse.ok(result)
