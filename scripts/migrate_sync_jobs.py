"""Create only the additive Mercado Livre background synchronization job table.

Run once against a backed-up PostgreSQL database before starting the updated API:
    python -m scripts.migrate_sync_jobs
"""
from app.database.connection import engine
from app.database.models import Base


def main():
    Base.metadata.tables["mercado_livre_sync_jobs"].create(bind=engine, checkfirst=True)
    print("mercado_livre_sync_jobs: pronta (dados anteriores preservados).")


if __name__ == "__main__":
    main()
