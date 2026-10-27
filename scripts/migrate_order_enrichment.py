"""Safe, repeatable additive migration for order enrichment.

Run from /var/www/fluxhub/backend with the production virtual environment:
    .venv/bin/python -m scripts.migrate_order_enrichment

Never drops tables or changes sellers, products, sync jobs or OAuth credentials.
"""
from app.database.order_enrichment_schema import ensure_order_enrichment_schema


def main() -> None:
    for name in ensure_order_enrichment_schema():
        print(f"{name}: pronta (dados anteriores preservados).")


if __name__ == "__main__":
    main()
