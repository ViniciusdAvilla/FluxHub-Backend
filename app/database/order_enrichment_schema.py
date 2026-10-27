"""Bootstraps ONLY additive order-enrichment tables; never touches OAuth data.

Invoked on FastAPI startup before the background sync worker begins. A PostgreSQL
transaction-scoped advisory lock serializes concurrent Uvicorn worker startup.
"""
from sqlalchemy import inspect, text
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


def ensure_sync_job_schema(db_engine: Engine | None = None) -> tuple[str, ...]:
    """Repair legacy sync-job columns without deleting jobs or OAuth connections.

    `create(checkfirst=True)` alone does not add columns to existing tables.
    Older VPS installations are missing these durable scroll checkpoints.
    """
    target = db_engine if db_engine is not None else engine
    table_name = "mercado_livre_sync_jobs"
    required = {
        "pending_listing_ids": "JSON",
        "pending_next_scroll_id": "TEXT",
    }
    with target.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _SCHEMA_LOCK_ID},
            )
        Base.metadata.tables[table_name].create(bind=conn, checkfirst=True)
        present = {column["name"] for column in inspect(conn).get_columns(table_name)}
        for name, sql_type in required.items():
            if name not in present:
                maybe_if_not_exists = (
                    "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
                )
                # Identifiers/types are fixed literals above; never user input.
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN "
                    f"{maybe_if_not_exists}{name} {sql_type}"
                ))
    return tuple(required)
