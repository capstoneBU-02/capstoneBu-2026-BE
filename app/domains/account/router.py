from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db  # 프로젝트 실제 get_db 경로 확인
from app.core.response import ApiResponse  # 프로젝트 실제 ApiResponse 경로 확인
from app.domains.account import service
from app.domains.account.models import Workspace
from app.domains.account.schemas import (
    TesterLoginRequest,
    TesterLoginResponse,
    WorkspaceMeResponse,
)

router = APIRouter(tags=["account"])


def get_current_workspace(
    ws_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Workspace:
    return service.get_workspace_by_token(db, ws_token)


@router.post("/auth/tester-login", response_model=ApiResponse[TesterLoginResponse])
def tester_login(
    body: TesterLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ApiResponse[TesterLoginResponse]:
    data, raw_token = service.login_with_tester_code(db, body.tester_code)
    response.set_cookie(
        key="ws_token",
        value=raw_token,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 14,
    )
    return ApiResponse.ok(data)


@router.get("/workspaces/me", response_model=ApiResponse[WorkspaceMeResponse])
def get_my_workspace(
    workspace: Workspace = Depends(get_current_workspace),
) -> ApiResponse[WorkspaceMeResponse]:
    data = WorkspaceMeResponse(
        workspace_id=workspace.workspace_id,
        owner_type=workspace.owner_type,
        session_active=True,
        created_at=workspace.created_at,
    )
    return ApiResponse.ok(data)
