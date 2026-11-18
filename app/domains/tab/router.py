from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.processing.sources import get_current_workspace_id
from app.domains.tab import service
from app.domains.tab.schemas import (
    ConditionsResponse,
    TabCreatedResponse,
    TabCreateRequest,
    TabDownloadResponse,
    TabListResponse,
    TabResponse,
    TabResultResponse,
    TabRetryResponse,
)

router = APIRouter(tags=["tab"])


@router.get("/tabs/conditions", response_model=ApiResponse[ConditionsResponse])
def get_conditions(
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[ConditionsResponse]:
    """지원 연주 조건 (튜닝·카포·결과 유형)."""
    return ApiResponse.ok(service.supported_conditions())


@router.post(
    "/segments/{segment_id}/tabs",
    status_code=202,
    response_model=ApiResponse[TabCreatedResponse],
)
def request_tab(
    segment_id: int,
    response: Response,
    body: TabCreateRequest | None = None,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TabCreatedResponse]:
    """타브 생성 요청. 새 작업이면 202, 같은 조건의 완료 결과를 재사용하면 200."""
    result = service.request_tab(db, workspace_id, segment_id, body or TabCreateRequest())
    if result.is_reused:
        response.status_code = 200
    return ApiResponse.ok(service.to_created_response(result))


@router.get("/segments/{segment_id}/tabs", response_model=ApiResponse[TabListResponse])
def list_tabs(
    segment_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TabListResponse]:
    """구간의 타브 목록 (최신 version부터)."""
    return ApiResponse.ok(service.list_tabs(db, workspace_id, segment_id))


@router.get("/tabs/{tab_id}", response_model=ApiResponse[TabResponse])
def get_tab(
    tab_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TabResponse]:
    """타브 상태 조회 (폴링). 작업 실패도 200이고 failure로 알린다."""
    tab = service.get_owned_tab(db, workspace_id, tab_id)
    return ApiResponse.ok(service.to_tab_response(db, tab))


@router.get("/tabs/{tab_id}/result", response_model=ApiResponse[TabResultResponse])
def get_tab_result(
    tab_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TabResultResponse]:
    """타브 결과 (success·needs_review일 때)."""
    return ApiResponse.ok(service.get_tab_result(db, workspace_id, tab_id))


@router.post("/tabs/{tab_id}/retry", status_code=202, response_model=ApiResponse[TabRetryResponse])
def retry_tab(
    tab_id: int,
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TabRetryResponse]:
    """실패한 단계부터 다시 실행한다."""
    return ApiResponse.ok(service.retry_tab(db, workspace_id, tab_id))


@router.get("/tabs/{tab_id}/download", response_model=ApiResponse[TabDownloadResponse])
def download_tab(
    tab_id: int,
    file_format: str = Query("musicxml", alias="format"),
    db: Session = Depends(get_db),
    workspace_id: int = Depends(get_current_workspace_id),
) -> ApiResponse[TabDownloadResponse]:
    """MusicXML 다운로드 URL. PDF는 프론트에서 만든다."""
    return ApiResponse.ok(service.get_download(db, workspace_id, tab_id, file_format))
