"""Validação de importação XLSX para o endpoint de produtos por empresa."""
from io import BytesIO

from pydantic import ValidationError

from app.schemas.product import Product

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_ROWS = 10_000
EXCEL_SOURCE_KEY = "excel"
SHEET_NAME = "Produtos_Ads"

REQUIRED_COLUMNS = (
    "SKU", "Nome do Produto", "Categoria", "Marca",
    "Preço Original (R$)", "Preço Promocional (R$)", "Desconto (%)",
    "Estoque Atual", "Vendas (30d)", "Avaliação", "Frete Grátis", "Condição",
)


class ExcelImportError(ValueError):
    """Planilha inválida ou incompatível com o catálogo FluxHub."""


def parse_product_workbook(content: bytes) -> list[Product]:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ExcelImportError("O arquivo deve ser XLSX e ter no máximo 15 MB.")

    try:
        # Import only when Excel is used; ordinary API startup avoids pandas.
        import pandas as pd
        data = pd.read_excel(BytesIO(content), sheet_name=SHEET_NAME, engine="openpyxl")
    except Exception as exc:
        raise ExcelImportError(
            f"Não foi possível ler a aba '{SHEET_NAME}' do arquivo XLSX."
        ) from exc

    missing = [name for name in REQUIRED_COLUMNS if name not in data.columns]
    if missing:
        raise ExcelImportError("Colunas obrigatórias ausentes: " + ", ".join(missing))
    if data.empty:
        raise ExcelImportError("A planilha não contém produtos.")
    if len(data) > MAX_ROWS:
        raise ExcelImportError("A planilha excede 10.000 produtos.")

    products = []
    for row_number, (_, row) in enumerate(data.iterrows(), start=2):
        if pd.isna(row["SKU"]) or not str(row["SKU"]).strip():
            raise ExcelImportError(f"SKU inválido na linha {row_number}.")
        try:
            products.append(
                Product(
                    external_id=str(row["SKU"]).strip(),
                    title=row["Nome do Produto"],
                    category=row["Categoria"],
                    brand=row["Marca"],
                    original_price=row["Preço Original (R$)"],
                    promotional_price=row["Preço Promocional (R$)"],
                    discount_rate=row["Desconto (%)"],
                    stock_qty=row["Estoque Atual"],
                    sales_30d=row["Vendas (30d)"],
                    rating=row["Avaliação"],
                    is_free_shipping=str(row["Frete Grátis"]).strip().casefold() == "sim",
                    item_condition=row["Condição"],
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ExcelImportError(f"Dados inválidos na linha {row_number}.") from exc

    return products
