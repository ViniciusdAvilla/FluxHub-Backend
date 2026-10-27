"""Safe additive migration for enriched order rows and public listing photos.

Run ONCE on the VPS from /var/www/fluxhub/backend using its .venv:
    .venv/bin/python -m scripts.migrate_order_enrichment

This creates only new tables. Existing products, listings, orders and jobs
are never dropped or reset.
"""
from app.database.connection import engine
from app.database.models import Base


def main() -> None:
    for name in (
        "mercado_livre_listing_photos",
        "mercado_livre_order_lines",
        "mercado_livre_buyer_orders",
        "mercado_livre_buyer_access_audit",
    ):
        Base.metadata.tables[name].create(bind=engine, checkfirst=True)
        print(f"{name}: pronta (dados anteriores preservados).")


if __name__ == "__main__":
    main()
