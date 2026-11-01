"""Independent OAuth/MCP tables. Does not alter Mercado Livre connections."""
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class McpClient(Base):
    __tablename__ = "fluxhub_mcp_clients"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    redirect_uris: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class McpAuthorizationRequest(Base):
    __tablename__ = "fluxhub_mcp_authorization_requests"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("fluxhub_mcp_clients.id", ondelete="CASCADE"), index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    challenge: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(512))
    scope: Mapped[str] = mapped_column(String(128), default="fluxhub:read")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("fluxhub_users.id", ondelete="CASCADE"), nullable=True, index=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpGrant(Base):
    __tablename__ = "fluxhub_mcp_grants"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("fluxhub_mcp_clients.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_version: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
