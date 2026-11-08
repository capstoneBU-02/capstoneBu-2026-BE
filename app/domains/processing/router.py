from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.processing import service
from app.domains.processing.schemas import (
    CancelResponse,
    RetryResponse,
    StemCreatedResponse,
    StemResponse,
)
from app.domains.processing.sources import get_current_workspace_id

router = APIRouter(tags=["processing"])


@router.post(
    "/segments/{segment_id}/stems",
    status_code=202,
    response_model=ApiResponse[StemCreatedResponse],
)
def request_stem(
    segment_id: int,
    response: Response,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[StemCreatedResponse]:
    """기타 음원 준비 요청. 새 작업이면 202, 같은 입력의 완료 결과를 재사용하면 200."""
    result = service.request_stem(db, workspace_id, segment_id)
    if result.is_reused:
        response.status_code = 200
    return ApiResponse.ok(service.to_created_response(result))


@router.get("/stems/{stem_id}", response_model=ApiResponse[StemResponse])
def get_stem(
    stem_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[StemResponse]:
    """기타 음원 상태 조회 (폴링). 작업 실패도 200이고 failure로 알린다."""
    stem = service.get_owned_stem(db, workspace_id, stem_id)
    return ApiResponse.ok(service.to_stem_response(db, stem))


@router.post("/stems/{stem_id}/retry", status_code=202, response_model=ApiResponse[RetryResponse])
def retry_stem(
    stem_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[RetryResponse]:
    """실패한 단계부터 다시 실행한다."""
    return ApiResponse.ok(service.retry_stem(db, workspace_id, stem_id))


@router.post("/stems/{stem_id}/cancel", response_model=ApiResponse[CancelResponse])
def cancel_stem(
    stem_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[CancelResponse]:
    """대기 중인 작업을 중단한다."""
    return ApiResponse.ok(service.cancel_stem(db, workspace_id, stem_id))
