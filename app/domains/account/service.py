import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.account.models import TesterCode, Workspace, WorkspaceSession
from app.domains.account.schemas import TesterLoginResponse


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def login_with_tester_code(db: Session, raw_code: str) -> tuple[TesterLoginResponse, str]:
    code = raw_code.strip()
    if not code.startswith("TESTER-"):
        raise BusinessException(ErrorCode.INVALID_CODE_FORMAT)

    code_hash = hash_token(code)
    stmt = select(TesterCode).where(
        TesterCode.code_hash == code_hash,
        TesterCode.status == "ACTIVE",
    )
    tester_code = db.execute(stmt).scalar_one_or_none()
    if tester_code is None:
        raise BusinessException(ErrorCode.TESTER_CODE_NOT_FOUND)

    now = datetime.now(UTC)
    if tester_code.expired_at is not None and tester_code.expired_at <= now:
        raise BusinessException(ErrorCode.TESTER_CODE_NOT_FOUND)

    workspace = db.get(Workspace, tester_code.workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        raise BusinessException(ErrorCode.TESTER_CODE_NOT_FOUND)

    # 기존 세션 존재 여부로 재접속 판별 (ACC-07)
    prev_stmt = (
        select(WorkspaceSession)
        .where(WorkspaceSession.workspace_id == workspace.workspace_id)
        .limit(1)
    )
    is_reconnected = db.execute(prev_stmt).scalar_one_or_none() is not None

    raw_token = secrets.token_urlsafe(32)
    session = WorkspaceSession(
        workspace_id=workspace.workspace_id,
        session_token_hash=hash_token(raw_token),
        expires_at=now + timedelta(days=14),
    )
    db.add(session)
    db.commit()

    response_data = TesterLoginResponse(
        workspace_id=workspace.workspace_id,
        owner_type=workspace.owner_type,
        is_reconnected=is_reconnected,
        notice_message="본 코드는 개인 전용 테스터 코드로, 타인과 공유할 수 없습니다.",
        created_at=now,
    )
    return response_data, raw_token


def get_workspace_by_token(db: Session, raw_token: str | None) -> Workspace:
    if not raw_token:
        raise BusinessException(ErrorCode.UNAUTHORIZED_SESSION)

    token_hash = hash_token(raw_token)
    stmt = select(WorkspaceSession).where(
        WorkspaceSession.session_token_hash == token_hash,
        WorkspaceSession.expires_at > datetime.now(UTC),
    )
    session = db.execute(stmt).scalar_one_or_none()
    if session is None:
        raise BusinessException(ErrorCode.UNAUTHORIZED_SESSION)

    workspace = db.get(Workspace, session.workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        raise BusinessException(ErrorCode.UNAUTHORIZED_SESSION)

    return workspace
