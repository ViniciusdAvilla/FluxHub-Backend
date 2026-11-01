"""Create only new MCP tables with advisory lock, leaving existing OAuth untouched."""
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.database.connection import engine
from app.database.mcp_models import McpClient, McpAuthorizationRequest, McpGrant


def ensure_mcp_schema(db_engine: Engine | None = None) -> None:
    target = db_engine if db_engine is not None else engine
    with target.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                         {"lock_id": 5739431260923})
        for model in (McpClient, McpAuthorizationRequest, McpGrant):
            model.__table__.create(bind=conn, checkfirst=True)
