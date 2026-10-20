"""Additive bootstrap migration for existing databases; back up PostgreSQL first.

For a long-lived deployment replace with reviewed Alembic revision.
"""
from sqlalchemy import inspect, text
from app.database.connection import engine
from app.database.models import Base


def main():
    tables = inspect(engine).get_table_names()
    if "fluxhub_users" in tables:
        fields = {col["name"] for col in inspect(engine).get_columns("fluxhub_users")}
        with engine.begin() as conn:
            if "is_admin" not in fields:
                conn.execute(text("ALTER TABLE fluxhub_users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
            if "is_active" not in fields:
                conn.execute(text("ALTER TABLE fluxhub_users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
            if "token_version" not in fields:
                conn.execute(text("ALTER TABLE fluxhub_users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))
    if "products" in tables:
        product_fields = {col["name"] for col in inspect(engine).get_columns("products")}
        with engine.begin() as conn:
            if "company_id" not in product_fields:
                conn.execute(text("ALTER TABLE products ADD COLUMN company_id INTEGER"))
            if "source_key" not in product_fields:
                conn.execute(text("ALTER TABLE products ADD COLUMN source_key VARCHAR(150)"))
    Base.metadata.create_all(engine)
    print("Esquema atualizado (sem remover tabelas existentes).")


if __name__ == "__main__":
    main()
