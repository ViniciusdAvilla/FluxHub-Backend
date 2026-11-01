"""Risk-based cost CSV import tests: tenant isolation, duplicates, preview integrity."""
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.models import (
    UserModel, MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderLineModel as Line, MercadoLivreProductCostModel as Cost,
)
from app.services.cost_import import (
    CostImportError, analyze_cost_import, apply_cost_import, parse_csv, preview_token,
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr("test_cost_import_signing_secret_12345678901234567890"))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (UserModel, Listing, Line, Cost):
        model.__table__.create(bind=engine, checkfirst=True)
    with Session(engine) as session:
        session.add_all([
            UserModel(id=1, email="one@example.test", password_hash="hash", is_active=True),
            UserModel(id=2, email="two@example.test", password_hash="hash", is_active=True),
            Listing(user_id=1, mercado_livre_user_id=123, external_id="MLB001",
                    title="Filtro do motor", price=180.00, status="active"),
            Listing(user_id=1, mercado_livre_user_id=123, external_id="MLB002",
                    title="Lâmpada", price=40.00, status="active"),
            Listing(user_id=1, mercado_livre_user_id=123, external_id="MLB003",
                    title="Correia", price=80.00, status="active"),
            Listing(user_id=2, mercado_livre_user_id=456, external_id="MLB099",
                    title="Outro vendedor", price=390.00, status="active"),
        ])
        session.flush()
        session.add_all([
            Line(user_id=1, mercado_livre_user_id=123, order_external_id="order-1",
                 line_number=0, item_id="MLB001", sku="REF-A", title="Filtro do motor",
                 quantity=1, unit_price=180.00),
            Line(user_id=1, mercado_livre_user_id=123, order_external_id="order-2",
                 line_number=0, item_id="MLB002", sku="REF-B", title="Lâmpada",
                 quantity=1, unit_price=40.00),
            Line(user_id=1, mercado_livre_user_id=123, order_external_id="order-3",
                 line_number=0, item_id="MLB003", sku="REF-MULTI", title="Correia",
                 quantity=1, unit_price=80.00),
            Line(user_id=1, mercado_livre_user_id=123, order_external_id="order-4",
                 line_number=0, item_id="MLB002", sku="REF-MULTI", title="Lâmpada",
                 quantity=1, unit_price=40.00),
            Line(user_id=2, mercado_livre_user_id=456, order_external_id="order-5",
                 line_number=0, item_id="MLB099", sku="REF-OTHER", title="Outro",
                 quantity=1, unit_price=390.00),
            Cost(user_id=1, external_id="MLB001", unit_cost=7.0, manufacturer="Preservar",
                 tax_percent=8, shipping_cost=3, other_cost=1),
        ])
        session.commit()
        yield session
    engine.dispose()


def _file(lines):
    header = "Cód.Item;Descrição;Referência;Saldo;Preço Médio;Novo Marca;\r\n"
    return (header + "\r\n".join(lines) + "\r\n").encode("cp1252")


def test_csv_portuguese_encoding_and_preserved_identifiers():
    content = _file(["00123;Filtro de óleo;REF-A;10;12,35;1"])
    row = parse_csv(content)[0]
    assert row.code == "00123"
    assert row.reference == "REF-A"
    assert str(row.amount) == "12.35"
    assert row.name == "Filtro de óleo"


def test_import_requires_preview_and_never_updates_marketplace_prices(db):
    file = _file([
        "0001;Filtro;REF-A;1;12,35;5",           # exact sku, eligible
        "0002;Lamp;REF-B;1;15,00;5",             # duplicate with next
        "0003;Lamp;REF-B;1;17,00;5",
        "0004;Any;REF-MULTI;1;18,00;5",         # ambiguous sku
        "0005;Other;REF-OTHER;1;29,00;5",       # belongs to another tenant
        "0006;Unknown;REF-MISSING;1;22,00;5",   # unmatched
        "0007;Placeholder;MLB002;1;0,01;5",    # placeholder, skipped
    ])
    result = analyze_cost_import(db, 1, file)
    assert result["ready"] == 1
    assert result["summary"]["duplicado"] == 2
    assert result["summary"]["ambiguo"] == 1
    assert result["summary"]["nao_encontrado"] == 2
    assert result["summary"]["revisar_valor"] == 1
    token = preview_token(1, result)
    with pytest.raises(HTTPException):
        apply_cost_import(db, 2, file, token)
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB001")) == 7
    with pytest.raises(HTTPException):
        apply_cost_import(db, 1, file.replace(b"12,35", b"13,35"), token)
    response = apply_cost_import(db, 1, file, token)
    assert response["updated"] == 1
    cost = db.scalar(select(Cost).where(Cost.user_id == 1, Cost.external_id == "MLB001"))
    assert cost.unit_cost == 12.35
    assert cost.manufacturer == "Preservar"
    assert cost.tax_percent == 8
    assert cost.shipping_cost == 3
    assert db.scalar(select(Listing.price).where(
        Listing.user_id == 1, Listing.external_id == "MLB001")) == 180
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 2, Cost.external_id == "MLB099")) is None
    repeat = analyze_cost_import(db, 1, file)
    assert repeat["ready"] == 0
    assert repeat["summary"]["sem_alteracao"] == 1


def test_direct_mlb_match_creates_only_cost_without_changing_stock(db):
    file = ("MLB;Preço Médio\nMLB003;32,20\n").encode()
    result = analyze_cost_import(db, 1, file)
    assert result["ready"] == 1
    assert result["rows"][0]["match_type"] == "mlb"
    response = apply_cost_import(db, 1, file, preview_token(1, result))
    assert response["new_cost_records"] == 1
    cost = db.scalar(select(Cost).where(Cost.user_id == 1, Cost.external_id == "MLB003"))
    assert cost.unit_cost == 32.2
    assert cost.tax_percent == 0
    assert db.scalar(select(Listing.price).where(
        Listing.user_id == 1, Listing.external_id == "MLB003")) == 80


def test_missing_price_header_and_duplicate_header_are_rejected():
    with pytest.raises(CostImportError):
        parse_csv(b"SKU;Estoque\nA;1\n")
    with pytest.raises(CostImportError):
        parse_csv(b"SKU;Preco Medio;Preco Medio\nA;3;4\n")
