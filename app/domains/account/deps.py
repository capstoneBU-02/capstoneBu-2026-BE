from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.account import service
from app.domains.account.models import Workspace


def get_current_workspace(
    ws_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Workspace:
    return service.get_workspace_by_token(db, ws_token)
