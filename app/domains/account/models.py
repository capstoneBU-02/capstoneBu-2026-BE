from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_entity import Base, SoftDeleteMixin, TimestampMixin


class Workspace(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False, default="TESTER_CODE")
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TesterCode(Base, TimestampMixin):
    __tablename__ = "tester_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceSession(Base, TimestampMixin):
    __tablename__ = "workspace_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
