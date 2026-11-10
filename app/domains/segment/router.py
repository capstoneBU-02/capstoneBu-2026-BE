from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import BusinessException, ErrorCode
from app.core.response import ApiResponse
from app.domains.segment import service
from app.domains.segment.schemas import (
    SegmentCreateRequest,
    SegmentResponse,
)

router = APIRouter(tags=["segment"])


@dataclass(frozen=True)
class VerifiedTrack:
    """인증과 접근 권한 검사를 통과한 트랙 정보."""

    track_id: int
    duration_ms: int


def get_verified_track(track_id: int) -> VerifiedTrack:
    """IN·ACC 연동 전까지 인증되지 않은 요청을 차단한다.

    TODO(IN·ACC):
    1. ws_token 쿠키 인증
    2. 트랙 존재 여부 확인
    3. 워크스페이스 접근 권한 확인
    4. 원곡 길이를 밀리초 단위로 반환
    """
    raise BusinessException(ErrorCode.UNAUTHORIZED)


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
