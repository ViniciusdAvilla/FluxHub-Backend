"""Bootstraps ONLY additive order-enrichment tables; never touches OAuth data.

Invoked on FastAPI startup before the background sync worker begins. A PostgreSQL
transaction-scoped advisory lock serializes concurrent Uvicorn worker startup.
"""
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.database.connection import engine
from app.database.models import Base

ORDER_ENRICHMENT_TABLES = (
    "mercado_livre_listing_photos",
    "mercado_livre_order_lines",
    "mercado_livre_buyer_orders",
    "mercado_livre_buyer_access_audit",
)
_SCHEMA_LOCK_ID = 5739431260921


def ensure_order_enrichment_schema(db_engine: Engine | None = None) -> tuple[str, ...]:
    """Create missing enrichment tables atomically, preserving every existing row."""
    target = db_engine if db_engine is not None else engine
    with target.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _SCHEMA_LOCK_ID},
            )
        for name in ORDER_ENRICHMENT_TABLES:
            Base.metadata.tables[name].create(bind=conn, checkfirst=True)
    return ORDER_ENRICHMENT_TABLES
