from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.account.deps import get_current_workspace
from app.domains.account.models import Workspace
from app.domains.input import service
from app.domains.input.schemas import TrackListResponse, TrackResponse, TrackUploadResponse

router = APIRouter(tags=["input"])


@router.post(
    "/tracks/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[TrackUploadResponse],
)
def upload_track(
    file: Annotated[UploadFile, File(...)],
    is_separated_stem: Annotated[bool, Form()] = False,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ApiResponse[TrackUploadResponse]:
    """트랙 음원 업로드 및 포맷·용량·길이(최대 5분)·저장 한도 검증."""
    result = service.upload_track(
        db=db,
        workspace_id=workspace.id,
        file=file,
        is_separated_stem=is_separated_stem,
    )
    return ApiResponse.ok(result)


@router.get("/tracks", response_model=ApiResponse[TrackListResponse])
def list_tracks(
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ApiResponse[TrackListResponse]:
    """내 워크스페이스의 원곡 목록 (최근에 올린 곡부터, 구간 수 포함)."""
    return ApiResponse.ok(service.list_tracks(db, workspace.id))


@router.get("/tracks/{track_id}", response_model=ApiResponse[TrackResponse])
def get_track(
    track_id: int,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ApiResponse[TrackResponse]:
    """원곡 상세. 없거나 다른 워크스페이스 곡이면 404 TRACK_NOT_FOUND."""
    return ApiResponse.ok(service.get_track(db, workspace.id, track_id))
