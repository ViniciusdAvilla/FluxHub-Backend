import sqlite3
import pytest
from app.config.settings import Settings
from scripts.migrate_legacy_sqlite import source_rows, normalize_products, normalize_finance


def test_reject_runtime_sqlite():
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(environment="production", database_url="sqlite:///bad.db")


def test_read_only_legacy_sources(tmp_path):
    product = tmp_path / "catalog.db"
    con = sqlite3.connect(product)
    con.execute("CREATE TABLE products (external_id TEXT, title TEXT, category TEXT)")
    con.execute("INSERT INTO products VALUES ('SKU01', 'Original', 'Ferramentas')")
    con.commit()
    con.close()
    normalized = normalize_products(source_rows(str(product), "products"))
    assert normalized["SKU01"]["title"] == "Original"
    assert normalized["SKU01"]["brand"] == "Sem marca"
    con = sqlite3.connect(product)
    assert con.execute("SELECT count(*) FROM products").fetchone()[0] == 1
    con.close()


def test_finance_mapping(tmp_path):
    path = tmp_path / "finance.sqlite"
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE finance ("productId" TEXT, "unitCost" REAL, "feePercent" REAL, "supplier" TEXT, "taxPercent" REAL, "shippingCost" REAL, "otherCost" REAL)')
    con.execute("INSERT INTO finance VALUES ('MLB1', 12.5, 16, 'Fábrica', 5, 3, 2)")
    con.commit()
    con.close()
    rows = normalize_finance(source_rows(str(path), "finance"))
    assert rows["MLB1"]["marketplace_fee_percent"] == 16
    assert rows["MLB1"]["manufacturer"] == "Fábrica"
