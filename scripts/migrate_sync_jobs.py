"""Additive Mercado Livre sync job migration; preserves all imported data.

Run against the existing database before restarting the updated service:
    python -m scripts.migrate_sync_jobs
"""
from sqlalchemy import text

from app.database.connection import engine
from app.database.models import Base


def main():
    Base.metadata.tables["mercado_livre_sync_jobs"].create(bind=engine, checkfirst=True)
    # Existing VPS installations already have the table: CREATE TABLE will not
    # add new columns, so extend it explicitly without dropping any rows.
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE mercado_livre_sync_jobs "
            "ADD COLUMN IF NOT EXISTS pending_listing_ids JSON"
        ))
        connection.execute(text(
            "ALTER TABLE mercado_livre_sync_jobs "
            "ADD COLUMN IF NOT EXISTS pending_next_scroll_id TEXT"
        ))
    print("mercado_livre_sync_jobs: atualizada (dados anteriores preservados).")


if __name__ == "__main__":
    main()
