from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TesterLoginRequest(BaseModel):
    tester_code: str = Field(min_length=4, max_length=64, examples=["TESTER-2026-A1B2"])


class TesterLoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: int
    owner_type: str
    is_reconnected: bool
    notice_message: str
    created_at: datetime


class WorkspaceMeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: int
    owner_type: str
    session_active: bool
    created_at: datetime
