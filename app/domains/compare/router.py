from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.compare import service
from app.domains.compare.schemas import (
    ComparisonCreatedResponse,
    ComparisonResponse,
    ComparisonResultResponse,
    PlaybackResponse,
    RegionCheckRequest,
    RegionCheckResponse,
    RetryResponse,
)
from app.domains.processing.sources import get_current_workspace_id

router = APIRouter(tags=["compare"])


@router.post(
    "/performances/{performance_id}/comparisons",
    status_code=202,
    response_model=ApiResponse[ComparisonCreatedResponse],
)
def request_comparison(
    performance_id: int,
    response: Response,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[ComparisonCreatedResponse]:
    """비교 요청. 새 작업이면 202, 같은 입력의 완료 결과를 재사용하면 200."""
    result = service.request_comparison(db, workspace_id, performance_id)
    if result.is_reused:
        response.status_code = 200
    return ApiResponse.ok(service.to_created_response(result))


@router.get("/comparisons/{comparison_id}", response_model=ApiResponse[ComparisonResponse])
def get_comparison(
    comparison_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[ComparisonResponse]:
    """비교 상태 조회 (폴링). 작업 실패도 200이고 failure로 알린다."""
    c = service.get_owned_comparison(db, workspace_id, comparison_id)
    return ApiResponse.ok(service.to_status_response(db, c))


@router.get(
    "/comparisons/{comparison_id}/result",
    response_model=ApiResponse[ComparisonResultResponse],
)
def get_result(
    comparison_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[ComparisonResultResponse]:
    """비교 결과와 다시 들어 볼 구간 (success·not_comparable일 때)."""
    return ApiResponse.ok(service.get_result(db, workspace_id, comparison_id))


@router.get("/comparisons/{comparison_id}/playback", response_model=ApiResponse[PlaybackResponse])
def get_playback(
    comparison_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[PlaybackResponse]:
    """원곡·기타 음원·내 연주 재생 정보."""
    return ApiResponse.ok(service.get_playback(db, workspace_id, comparison_id))


@router.patch(
    "/comparisons/{comparison_id}/regions/{region_id}",
    response_model=ApiResponse[RegionCheckResponse],
)
def check_region(
    comparison_id: int,
    region_id: int,
    body: RegionCheckRequest,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[RegionCheckResponse]:
    """다시 들어 볼 구간의 확인 여부를 저장한다."""
    return ApiResponse.ok(
        service.check_region(db, workspace_id, comparison_id, region_id, body.is_checked)
    )


@router.post(
    "/comparisons/{comparison_id}/retry",
    status_code=202,
    response_model=ApiResponse[RetryResponse],
)
def retry_comparison(
    comparison_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[RetryResponse]:
    """실패한 단계부터 다시 실행한다."""
    return ApiResponse.ok(service.retry_comparison(db, workspace_id, comparison_id))
