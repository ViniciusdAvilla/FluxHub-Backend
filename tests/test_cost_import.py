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
            Line(user_id=1, mercado_livre_user_id=123, order_external_id="order-6",
                 line_number=0, item_id="MLB003", sku="REF-C", title="Correia",
                 quantity=1, unit_price=80.00),
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
        "0001;Filtro;REF-A;1;12,35;5",           # exact sku, existing cost preserved
        "0002;Lamp;REF-B;1;15,00;5",             # duplicate with next
        "0003;Lamp;REF-B;1;17,00;5",
        "0004;Any;REF-MULTI;1;18,00;5",         # ambiguous sku
        "0005;Other;REF-OTHER;1;29,00;5",       # belongs to another tenant
        "0006;Unknown;REF-MISSING;1;22,00;5",   # unmatched
        "0007;Placeholder;MLB002;1;0,01;5",    # placeholder, skipped
        "0008;Correia;REF-C;1;19,85;5",       # only missing cost is eligible
    ])
    result = analyze_cost_import(db, 1, file)
    assert result["ready"] == 1
    assert result["summary"]["sem_alteracao"] == 1  # existing MLB001 cost 7.00 preserved
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
    assert cost.unit_cost == 7.0
    assert cost.manufacturer == "Preservar"
    assert cost.tax_percent == 8
    assert cost.shipping_cost == 3
    assert db.scalar(select(Listing.price).where(
        Listing.user_id == 1, Listing.external_id == "MLB001")) == 180
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 2, Cost.external_id == "MLB099")) is None
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB003")) == 19.85
    repeat = analyze_cost_import(db, 1, file)
    assert repeat["ready"] == 0
    assert repeat["summary"]["sem_alteracao"] == 2


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


def test_only_fills_zero_cost_and_preserves_registered_positive_cost(db):
    db.add(Cost(user_id=1, external_id="MLB002", unit_cost=0,
                manufacturer="Lâmpadas", tax_percent=12, shipping_cost=4,
                other_cost=2))
    db.commit()
    file = _file([
        "002;Lâmpada;REF-B;5;21,90;5",
        "001;Filtro;REF-A;2;999,99;5",
    ])
    preview = analyze_cost_import(db, 1, file)
    assert preview["ready"] == 1
    assert preview["summary"]["sem_alteracao"] == 1
    by_id = {r["item_id"]: r for r in preview["rows"]}
    assert by_id["MLB001"]["status"] == "sem_alteracao"
    assert by_id["MLB001"]["old_cost"] == 7.0
    assert by_id["MLB002"]["status"] == "atualizar"
    result = apply_cost_import(db, 1, file, preview_token(1, preview))
    assert result["filled"] == 1
    assert result["new_cost_records"] == 0
    assert result["filled_zero_costs"] == 1
    cost = db.scalar(select(Cost).where(Cost.user_id == 1, Cost.external_id == "MLB002"))
    assert cost.unit_cost == 21.9
    assert cost.manufacturer == "Lâmpadas"
    assert cost.tax_percent == 12
    assert cost.shipping_cost == 4
    assert cost.other_cost == 2
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB001")) == 7.0
    assert db.scalar(select(Listing.price).where(
        Listing.user_id == 1, Listing.external_id == "MLB002")) == 40.0


def test_file_with_only_existing_cost_has_nothing_to_import(db):
    file = _file(["001;Filtro;REF-A;1;58,70;5"])
    preview = analyze_cost_import(db, 1, file)
    assert preview["ready"] == 0
    assert preview["summary"]["sem_alteracao"] == 1
    with pytest.raises(HTTPException) as exc:
        apply_cost_import(db, 1, file, preview_token(1, preview))
    assert exc.value.status_code == 422
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB001")) == 7.0


def test_preview_invalidated_if_cost_was_filled_after_preview(db):
    file = _file(["003;Correia;REF-C;1;18,75;5"])
    preview = analyze_cost_import(db, 1, file)
    assert preview["ready"] == 1
    db.add(Cost(user_id=1, external_id="MLB003", unit_cost=17.0,
                manufacturer="", tax_percent=0, shipping_cost=0, other_cost=0))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        apply_cost_import(db, 1, file, preview_token(1, preview))
    assert exc.value.status_code == 409
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB003")) == 17.0


def test_erp_csv_ignores_other_columns_and_placeholder_prices():
    data = ("\r\n".join([
        "Cód.Item;Descrição;Referência;Saldo;Preço Médio;Novo Código de fábrica;Novo Marca;Desc. Marca;",
        "99999;ITEM CONVERSAO;;0;0,01;;999999;MARCA CONVERSAO;",
        "47522;CABO NEGATIVO;CONV-001-47522;123;42,45;;999999;MARCA CONVERSAO;",
    ]) + "\r\n").encode("cp1252")
    rows = parse_csv(data)
    assert len(rows) == 2
    assert rows[0].issue == "revisar_valor"
    assert rows[1].code == "47522"
    assert rows[1].reference == "CONV-001-47522"
    assert str(rows[1].amount) == "42.45"


def test_mlb_in_referencia_matches_exact_listing_owned_by_tenant(db):
    file = _file(["003;Correia;MLB003;1;25,40;5"])
    preview = analyze_cost_import(db, 1, file)
    assert preview["ready"] == 1
    assert preview["rows"][0]["item_id"] == "MLB003"
    assert preview["rows"][0]["match_type"] == "mlb"
    result = apply_cost_import(db, 1, file, preview_token(1, preview))
    assert result["filled"] == 1
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB003")) == 25.4


def test_document_excel_import_uses_precise_sku_and_preserves_positive_cost(db):
    from io import BytesIO
    from decimal import Decimal
    from openpyxl import Workbook
    from app.services.cost_documents import parse_cost_document

    book = Workbook()
    sheet = book.active
    sheet.append(["Relatório exportado do ERP"])
    sheet.append(["Cód.Item", "Descrição", "Referência", "Preço de venda", "Preço Médio"])
    sheet.append(["0001", "Filtro", "REF-A", 200.00, 99.99])  # existing cost unchanged
    sheet.append(["0002", "Correia", "REF-C", 88.00, 24.90])
    hidden = book.create_sheet("Antigos")
    hidden.append(["MLB", "Preço Médio"])
    hidden.append(["MLB002", 1.99])
    hidden.sheet_state = "hidden"
    file = BytesIO()
    book.save(file)
    content = file.getvalue()

    parsed = parse_cost_document(content, "base-precos.xlsx")
    assert len(parsed) == 2
    assert parsed[0].reference == "REF-A"
    assert parsed[1].amount == Decimal("24.90")
    result = analyze_cost_import(db, 1, content, "base-precos.xlsx")
    assert result["ready"] == 1
    assert result["summary"]["sem_alteracao"] == 1
    applied = apply_cost_import(db, 1, content, preview_token(1, result), "base-precos.xlsx")
    assert applied["updated"] == 1
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB001")) == 7.0
    assert db.scalar(select(Cost.unit_cost).where(
        Cost.user_id == 1, Cost.external_id == "MLB003")) == 24.90
    assert db.scalar(select(Listing.price).where(
        Listing.user_id == 1, Listing.external_id == "MLB003")) == 80.00


def test_word_docx_table_and_plain_text_support():
    from io import BytesIO
    from docx import Document
    from app.services.cost_documents import parse_cost_document

    document = Document()
    table = document.add_table(rows=1, cols=4)
    for index, value in enumerate(["SKU", "Descrição", "Preço Médio", "Preço de venda"]):
        table.rows[0].cells[index].text = value
    row = table.add_row().cells
    row[0].text = "REF-C"
    row[1].text = "Correia"
    row[2].text = "42,50"
    row[3].text = "180,00"
    file = BytesIO()
    document.save(file)
    records = parse_cost_document(file.getvalue(), "precos.docx")
    assert len(records) == 1
    assert records[0].reference == "REF-C"
    assert str(records[0].amount) == "42.50"

    document = Document()
    document.add_paragraph("SKU: REF-C | Custo de compra: R$ 29,25")
    file = BytesIO()
    document.save(file)
    records = parse_cost_document(file.getvalue(), "precos.docx")
    assert str(records[0].amount) == "29.25"


def test_pdf_text_and_xls_are_supported():
    from io import BytesIO
    from reportlab.pdfgen import canvas
    from app.services.cost_documents import parse_cost_document
    import xlwt

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(40, 780, "SKU: REF-C | Custo de compra: R$ 25,90")
    pdf.save()
    rows = parse_cost_document(buffer.getvalue(), "custos.pdf")
    assert len(rows) == 1
    assert rows[0].reference == "REF-C"
    assert str(rows[0].amount) == "25.90"

    book = xlwt.Workbook()
    sheet = book.add_sheet("Precos")
    for col, cell in enumerate(("MLB", "Custo de compra", "Preco de venda")):
        sheet.write(0, col, cell)
    sheet.write(1, 0, "MLB003")
    sheet.write(1, 1, 19.80)
    sheet.write(1, 2, 100.00)
    output = BytesIO()
    book.save(output)
    rows = parse_cost_document(output.getvalue(), "custos.xls")
    assert rows[0].mlb == "MLB003"
    assert str(rows[0].amount) == "19.8"


def test_document_rejects_sale_price_only_and_invalid_files():
    from app.services.cost_documents import parse_cost_document

    with pytest.raises(CostImportError, match="Nenhum custo"):
        parse_cost_document(b"SKU;Preco\nREF-C;500\n", "vendas.csv")
    with pytest.raises(CostImportError, match="Formato"):
        parse_cost_document(b"SKU;Custo\nREF-C;15\n", "precos.exe")
    with pytest.raises(CostImportError, match="PDF inv"):
        parse_cost_document(b"not-a-real-pdf", "precos.pdf")
    with pytest.raises(CostImportError, match="Excel/Word inv"):
        parse_cost_document(b"not-a-real-docx", "precos.docx")
    with pytest.raises(CostImportError, match="16 MB"):
        parse_cost_document(b"X" * (16 * 1024 * 1024 + 1), "large.csv")


def test_text_file_explicit_cost_pairs_only():
    from app.services.cost_documents import parse_cost_document
    text = "sku: ref-c | custo de compra: R$ 35,40\nsku: ref-a | preco de venda: 90,00"
    rows = parse_cost_document(text.encode(), "custos.txt")
    assert len(rows) == 1
    assert rows[0].reference == "REF-C"
    assert str(rows[0].amount) == "35.40"
