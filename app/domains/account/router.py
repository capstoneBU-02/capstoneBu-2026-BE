from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.response import ApiResponse
from app.domains.account import service
from app.domains.account.deps import get_current_workspace
from app.domains.account.models import Workspace
from app.domains.account.schemas import (
    TesterLoginRequest,
    TesterLoginResponse,
    WorkspaceMeResponse,
)

router = APIRouter(tags=["account"])


@router.post("/auth/tester-login", response_model=ApiResponse[TesterLoginResponse])
def tester_login(
    body: TesterLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ApiResponse[TesterLoginResponse]:
    settings = get_settings()
    data, raw_token = service.login_with_tester_code(db, body.tester_code)
    response.set_cookie(
        key="ws_token",
        value=raw_token,
        httponly=True,
        secure=settings.cookie_secure,
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
        workspace_id=workspace.id,
        owner_type=workspace.owner_type,
        session_active=True,
        created_at=workspace.created_at,
    )
    return ApiResponse.ok(data)
