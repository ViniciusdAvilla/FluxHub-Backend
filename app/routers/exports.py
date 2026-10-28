"""Tenant-scoped XLSX exports. No buyer personal data is included."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    UserModel, ProductModel,
    MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreOrderLineModel as OrderLine,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreProductCostModel as Cost,
)
from app.database.session import get_db

router = APIRouter(prefix="/exports", tags=["Exports"])

HEADERS = {
    "Produtos": ["Código", "Título", "Categoria", "Fabricante", "Preço original",
                 "Preço promocional", "Estoque", "Vendas 30d", "Origem"],
    "Pedidos": ["Pedido", "Vendedor", "Situação", "Valor bruto", "Data",
                "Cancelado"],
    "Itens vendidos": ["Pedido", "Vendedor", "Item", "Título", "SKU", "Quantidade",
                      "Preço unitário", "Comissão informada"],
    "Anúncios": ["Anúncio", "Vendedor", "Título", "Situação", "Tipo",
                 "Preço", "Visitas 30d", "Vendas 30d", "Atualizado em"],
    "Custos": ["Item", "Custo unidade", "Comissão configurada (%)", "ICMS configurado (%)",
              "Frete configurado", "Outros custos", "Fabricante", "Atualizado em"],
}


def excel_cell(value):
    if value is None:
        return None
    if isinstance(value, str):
        # Excel interprets leading '=' as a formula. Keep marketplace text inert.
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
        return value[:32767]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return value


def populate_sheet(workbook, name, records):
    sheet = workbook.create_sheet(name)
    sheet.append(HEADERS[name])
    for row in records:
        sheet.append([excel_cell(value) for value in row])


def generate_report(
    db: Session, user_id: int, report: str,
    account_id: int | None = None, days: int | None = None,
) -> bytes:
    """Write-only generation avoids materializing all seller records in Python."""
    workbook = Workbook(write_only=True)

    if report in ("complete", "products"):
        stmt = select(ProductModel).where(ProductModel.company_id == user_id).order_by(ProductModel.id)
        if account_id is not None:
            stmt = stmt.where(ProductModel.source_key == f"ml:{account_id}")
        populate_sheet(workbook, "Produtos", (
            (row.external_id, row.title, row.category, row.brand,
             row.original_price, row.promotional_price, row.stock_qty, row.sales_30d, row.source_key)
            for row in db.scalars(stmt).yield_per(500)
        ))
    if report in ("complete", "orders"):
        stmt = select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
        if account_id is not None:
            stmt = stmt.where(Order.mercado_livre_user_id == account_id)
        if days is not None:
            stmt = stmt.where(Order.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
        populate_sheet(workbook, "Pedidos", (
            (row.external_id, row.mercado_livre_user_id, row.status, row.total_amount,
             row.created_at, row.is_cancelled)
            for row in db.scalars(stmt).yield_per(500)
        ))
        line_stmt = select(OrderLine).where(OrderLine.user_id == user_id).order_by(OrderLine.id)
        if account_id is not None:
            line_stmt = line_stmt.where(OrderLine.mercado_livre_user_id == account_id)
        if days is not None:
            scoped = select(Order.external_id).where(
                Order.user_id == user_id,
                Order.mercado_livre_user_id == OrderLine.mercado_livre_user_id,
                Order.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
            line_stmt = line_stmt.where(OrderLine.order_external_id.in_(scoped))
        populate_sheet(workbook, "Itens vendidos", (
            (row.order_external_id, row.mercado_livre_user_id,
             row.item_id, row.title, row.sku, row.quantity, row.unit_price, row.sale_fee)
            for row in db.scalars(line_stmt).yield_per(500)
        ))
    if report in ("complete", "listings"):
        stmt = select(Listing).where(Listing.user_id == user_id).order_by(Listing.id)
        if account_id is not None:
            stmt = stmt.where(Listing.mercado_livre_user_id == account_id)
        populate_sheet(workbook, "Anúncios", (
            (row.external_id, row.mercado_livre_user_id, row.title, row.status,
             row.listing_type_id, row.price, row.visits_30d,
             row.units_sold_30d, row.captured_at)
            for row in db.scalars(stmt).yield_per(500)
        ))
    if report in ("complete", "costs"):
        stmt = select(Cost).where(Cost.user_id == user_id).order_by(Cost.id)
        populate_sheet(workbook, "Custos", (
            (row.external_id, row.unit_cost, row.marketplace_fee_percent, row.tax_percent,
             row.shipping_cost, row.other_cost, row.manufacturer, row.updated_at)
            for row in db.scalars(stmt).yield_per(500)
        ))

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@router.get("/{report}")
def download_xlsx(
    report: Literal["complete", "products", "orders", "listings", "costs"],
    account_id: int | None = Query(default=None, ge=1),
    days: int | None = Query(default=None, ge=1, le=365),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content = generate_report(db, user.id, report, account_id, days)
    name = f"fluxhub-{report}-{datetime.now(timezone.utc):%Y%m%d}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex",
        },
    )
