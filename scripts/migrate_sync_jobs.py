"""Idempotent schema repair for durable Mercado Livre sync job checkpoints.

Run from the backend deployment directory:
    .venv/bin/python -m scripts.migrate_sync_jobs

Never resets sync jobs, imported products, orders or OAuth credentials.
"""
from app.database.order_enrichment_schema import ensure_sync_job_schema


def main() -> None:
    columns = ensure_sync_job_schema()
    print("mercado_livre_sync_jobs: colunas verificadas (" +
          ", ".join(columns) + "); dados e conexões preservados.")


if __name__ == "__main__":
    main()
