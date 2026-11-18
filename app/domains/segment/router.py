from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import BusinessException, ErrorCode
from app.core.response import ApiResponse
from app.domains.account.deps import get_current_workspace
from app.domains.account.models import Workspace
from app.domains.input.models import Track
from app.domains.segment import service
from app.domains.segment.models import Segment
from app.domains.segment.schemas import (
    SegmentCreateRequest,
    SegmentResponse,
    SegmentUpdateRequest,
)

router = APIRouter(tags=["segment"])


@dataclass(frozen=True)
class VerifiedTrack:
    """인증과 접근 권한 검사를 통과한 트랙 정보."""

    track_id: int
    duration_ms: int


def get_verified_track(
    track_id: int,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> VerifiedTrack:
    """로그인한 워크스페이스의 트랙인지 검증한다."""

    track = db.get(Track, track_id)

    if track is None or track.workspace_id != workspace.id:
        raise BusinessException(ErrorCode.TRACK_NOT_FOUND)

    return VerifiedTrack(
        track_id=track.id,
        duration_ms=track.duration_ms,
    )


@router.post(
    "/tracks/{track_id}/segments",
    status_code=201,
    response_model=ApiResponse[SegmentResponse],
)
def create_segment(
    request: SegmentCreateRequest,
    db: Session = Depends(get_db),
    track: VerifiedTrack = Depends(get_verified_track),
) -> ApiResponse[SegmentResponse]:
    """새 구간 생성."""
    result = service.create_segment(
        db=db,
        track_id=track.track_id,
        request=request,
        track_duration_ms=track.duration_ms,
    )
    return ApiResponse.ok(result)


@router.patch(
    "/segments/{segment_id}",
    response_model=ApiResponse[SegmentResponse],
)
def update_segment(
    segment_id: int,
    request: SegmentUpdateRequest,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> ApiResponse[SegmentResponse]:
    """로그인한 워크스페이스의 구간 범위를 수정한다."""

    segment = db.get(Segment, segment_id)

    if segment is None:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)

    track = db.get(Track, segment.track_id)

    if track is None or track.workspace_id != workspace.id:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)

    result = service.update_segment(
        db=db,
        segment_id=segment_id,
        request=request,
        track_duration_ms=track.duration_ms,
    )

    return ApiResponse.ok(result)
