"""Additive Claude configuration table bootstrap; never modifies OAuth data."""
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.database.connection import engine
from app.database.models import Base

_LOCK_ID = 5739431260922


def ensure_claude_provider_schema(db_engine: Engine | None = None) -> None:
    target = db_engine if db_engine is not None else engine
    with target.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _LOCK_ID},
            )
        Base.metadata.tables["claude_provider_config"].create(
            bind=connection, checkfirst=True
        )
