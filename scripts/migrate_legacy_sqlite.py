"""Migrate *existing* legacy SQLite data to PostgreSQL, scoped to one FluxHub user.

Safe by default: no writes without --apply. SQLite sources are opened READ ONLY.
Usage:
 python -m scripts.migrate_legacy_sqlite --products-db /absolute/legacy.db \\
   --finance-db /absolute/finance.sqlite --user-email seller@example.com
 # after inspecting counts and BACKUPS: append --apply

Never use this tool to merge multiple customers' unowned SQLite catalogs blindly.
"""
import argparse
import sqlite3
from pathlib import Path
from sqlalchemy import select
from app.config.settings import settings
from app.database.models import (
    UserModel, ProductModel, CompanyDataSourceModel,
    MercadoLivreProductCostModel as Cost,
)
from app.database.session import SessionLocal

SOURCE = "sqlite-legacy"


def source_rows(filename: str, table: str) -> list[dict]:
    path = Path(filename).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Origem não é arquivo: {path}")
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        found = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not found:
            raise ValueError(f"Tabela {table} não encontrada em {path}")
        # Table name is fixed by the caller, never derived from user input.
        return [dict(r) for r in con.execute(
            'SELECT * FROM "products"' if table == "products"
            else 'SELECT * FROM "finance"')]
    finally:
        con.close()


def normalize_products(rows: list[dict]) -> dict[str, dict]:
    items = {}
    for row in rows:
        external_id = str(row.get("external_id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not external_id or len(external_id) > 100 or not title:
            raise ValueError("Produto sem external_id/title válido; não migrou nada.")
        if external_id in items:
            raise ValueError(f"SKU duplicado na origem: {external_id}")
        items[external_id] = {
            "title": title[:255],
            "category": str(row.get("category") or "Sem categoria")[:100],
            "brand": str(row.get("brand") or "Sem marca")[:100],
            "original_price": row.get("original_price"),
            "promotional_price": row.get("promotional_price"),
            "discount_rate": row.get("discount_rate"),
            "stock_qty": row.get("stock_qty"),
            "sales_30d": row.get("sales_30d"),
            "rating": row.get("rating"),
            "is_free_shipping": row.get("is_free_shipping"),
            "item_condition": str(row.get("item_condition") or "unknown")[:50],
        }
    return items


def normalize_finance(rows: list[dict]) -> dict[str, dict]:
    items = {}
    for row in rows:
        external_id = str(row.get("productId") or "").strip()
        if not external_id or len(external_id) > 100:
            raise ValueError("Custo financeiro sem productId válido.")
        if external_id in items:
            raise ValueError(f"Produto financeiro duplicado na origem: {external_id}")
        items[external_id] = {
            "unit_cost": float(row["unitCost"]),
            "marketplace_fee_percent": float(row["feePercent"]),
            "manufacturer": str(row.get("supplier") or "")[:120],
            "tax_percent": float(row["taxPercent"]),
            "shipping_cost": float(row["shippingCost"]),
            "other_cost": float(row["otherCost"]),
        }
    return items


def migrate(products: dict, finance: dict, email: str, apply: bool) -> dict:
    if not settings.database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Destino NÃO é PostgreSQL; migração interrompida.")
    with SessionLocal() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == email.lower().strip()))
        if not user:
            raise ValueError("O usuário de destino não existe. Crie-o primeiro.")
        company_id = user.id
        counts = {"products_source": len(products), "finance_source": len(finance),
                  "products_inserted": 0, "products_updated": 0,
                  "costs_inserted": 0, "costs_skipped": 0, "applied": apply}
        if not apply:
            print("DRY RUN: nenhuma escrita foi feita.")
            return counts
        source = db.scalar(select(CompanyDataSourceModel).where(
            CompanyDataSourceModel.company_id == company_id,
            CompanyDataSourceModel.provider == "sqlite_legacy",
            CompanyDataSourceModel.account_key == SOURCE))
        if source is None and products:
            source = CompanyDataSourceModel(
                company_id=company_id, provider="sqlite_legacy",
                account_key=SOURCE, display_name="Catálogo SQLite legado",
                connected=True, enabled=True)
            db.add(source)
        for external_id, fields in products.items():
            existing = db.scalar(select(ProductModel).where(
                ProductModel.company_id == company_id,
                ProductModel.source_key == SOURCE,
                ProductModel.external_id == external_id))
            if existing is None:
                db.add(ProductModel(company_id=company_id, source_key=SOURCE,
                                    external_id=external_id, **fields))
                counts["products_inserted"] += 1
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
                counts["products_updated"] += 1
        for external_id, fields in finance.items():
            existing_cost = db.scalar(select(Cost).where(
                Cost.user_id == company_id, Cost.external_id == external_id))
            # Do not overwrite newer costs in PostgreSQL with stale finance.sqlite values.
            if existing_cost:
                counts["costs_skipped"] += 1
                continue
            db.add(Cost(user_id=company_id, external_id=external_id, **fields))
            counts["costs_inserted"] += 1
        db.commit()
        return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products-db", help="Caminho absoluto do SQLite de produtos")
    parser.add_argument("--finance-db", help="Caminho absoluto do SQLite financeiro")
    parser.add_argument("--user-email", required=True, help="Conta FluxHub dona dos dados")
    parser.add_argument("--apply", action="store_true",
                        help="Escreve no PostgreSQL após backups e validação do --dry-run")
    args = parser.parse_args()
    if not args.products_db and not args.finance_db:
        parser.error("Informe --products-db e/ou --finance-db.")
    products = normalize_products(source_rows(args.products_db, "products")) if args.products_db else {}
    finance = normalize_finance(source_rows(args.finance_db, "finance")) if args.finance_db else {}
    print("Origem: %s produtos, %s custos; destino: %s" % (
        len(products), len(finance), args.user_email.strip().lower()))
    result = migrate(products, finance, args.user_email, apply=args.apply)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
