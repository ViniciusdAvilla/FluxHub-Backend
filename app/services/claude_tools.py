"""Read-only, tenant-scoped tools shared by Claude chat and the remote MCP server.

Every query independently enforces user_id. No buyer PII or OAuth secrets are
ever returned. All values are taken from persisted snapshots and may be partial.
"""
from datetime import datetime, timedelta, timezone, date

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.database.models import (
    MercadoLivreAdsDailySnapshotModel as Ads,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderLineModel as Line,
    MercadoLivreOrderSnapshotModel as Order,
    ProductModel as Product,
)

TOOL_DEFINITIONS = [
    {
        "name": "resumo_operacao",
        "description": "Resumo da operação Mercado Livre: anúncios, estoque, pedidos pagos e faturamento bruto do período, com ressalvas de cobertura.",
        "input_schema": {"type": "object", "properties": {
            "dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30}
        }, "additionalProperties": False},
    },
    {
        "name": "consultar_vendas",
        "description": "Resumo de pedidos e vendas pagas com valor bruto de um período. Não representa lucro nem valor recebido.",
        "input_schema": {"type": "object", "properties": {
            "dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30}
        }, "additionalProperties": False},
    },
    {
        "name": "produtos_mais_vendidos",
        "description": "Lista dos produtos vendidos em pedidos pagos, ordenada por unidades e faturamento bruto. Sem dados do comprador.",
        "input_schema": {"type": "object", "properties": {
            "dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
            "limite": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}
        }, "additionalProperties": False},
    },
    {
        "name": "consultar_estoque",
        "description": "Lista produtos esgotados ou com estoque baixo no catálogo armazenado.",
        "input_schema": {"type": "object", "properties": {
            "limite": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}
        }, "additionalProperties": False},
    },
    {
        "name": "resumo_product_ads",
        "description": "Gastos e receita atribuída a Product Ads nos snapshots disponíveis. Pode haver dados ausentes.",
        "input_schema": {"type": "object", "properties": {
            "dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30}
        }, "additionalProperties": False},
    },
]

def _number(arguments: dict, key: str, default: int, maximum: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= maximum):
        raise ValueError(f"{key} deve ser inteiro entre 1 e {maximum}.")
    return value

def _sales(db: Session, user_id: int, days: int) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    paid = and_(Order.status == "paid", Order.is_cancelled.is_(False))
    total, count_paid, gross = db.execute(select(
        func.count(Order.id),
        func.count(Order.id).filter(paid),
        func.coalesce(func.sum(Order.total_amount).filter(paid), 0),
    ).where(Order.user_id == user_id, Order.created_at >= cutoff)).one()
    return {
        "periodo_dias": days,
        "pedidos_armazenados": int(total or 0),
        "pedidos_pagos": int(count_paid or 0),
        "faturamento_bruto_pedidos_pagos_brl": round(float(gross or 0), 2),
        "origem": "PostgreSQL FluxHub - pedidos sincronizados",
        "aviso": "Histórico pode estar incompleto; faturamento bruto não é lucro líquido nem valor liquidado.",
    }

def execute_tool(db: Session, user_id: int, name: str, arguments: dict | None = None) -> dict:
    if name not in {entry["name"] for entry in TOOL_DEFINITIONS}:
        raise ValueError("Ferramenta não autorizada.")
    args = arguments if isinstance(arguments, dict) else {}
    allowed = next(item for item in TOOL_DEFINITIONS if item["name"] == name)
    if set(args) - set(allowed["input_schema"]["properties"]):
        raise ValueError("Parâmetros não reconhecidos.")

    if name == "consultar_vendas":
        return _sales(db, user_id, _number(args, "dias", 30, 365))

    if name == "resumo_operacao":
        days = _number(args, "dias", 30, 365)
        products, out = db.execute(select(
            func.count(Product.id),
            func.count(Product.id).filter(Product.stock_qty == 0),
        ).where(Product.company_id == user_id)).one()
        snapshot_date = db.scalar(select(func.max(Listing.captured_at)).where(
            Listing.user_id == user_id))
        return {
            **_sales(db, user_id, days),
            "produtos_catalogo": int(products or 0),
            "produtos_esgotados": int(out or 0),
            "anuncios_atualizados_em": snapshot_date.isoformat() if snapshot_date else None,
        }

    if name == "consultar_estoque":
        limit = _number(args, "limite", 10, 20)
        rows = db.execute(select(
            Product.external_id, Product.title, Product.stock_qty,
        ).where(Product.company_id == user_id, Product.stock_qty.is_not(None),
                Product.stock_qty <= 5).order_by(Product.stock_qty.asc(), Product.title.asc())
                          .limit(limit)).all()
        return {
            "origem": "PostgreSQL FluxHub - catálogo armazenado",
            "estoque_baixo_ate": 5,
            "produtos": [{"id": identifier, "nome": title, "quantidade": qty}
                         for identifier, title, qty in rows],
            "aviso": "Quantidade corresponde à última sincronização; não é estoque em tempo real.",
        }

    if name == "produtos_mais_vendidos":
        days = _number(args, "dias", 30, 365)
        limit = _number(args, "limite", 10, 20)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = db.execute(select(
            Line.item_id, Line.title,
            func.sum(Line.quantity).label("unidades"),
            func.sum(Line.quantity * Line.unit_price).label("bruto"),
        ).join(Order, and_(
            Order.user_id == Line.user_id,
            Order.mercado_livre_user_id == Line.mercado_livre_user_id,
            Order.external_id == Line.order_external_id,
        )).where(
            Order.user_id == user_id,
            Line.user_id == user_id,
            Order.created_at >= since,
            Order.status == "paid",
            Order.is_cancelled.is_(False),
        ).group_by(Line.item_id, Line.title).order_by(
            desc("unidades"), desc("bruto"),
        ).limit(limit)).all()
        return {
            "periodo_dias": days,
            "produtos": [{"id": item_id, "nome": title, "unidades": int(units),
                          "vendas_brutas_brl": round(float(gross), 2)}
                         for item_id, title, units, gross in rows],
            "origem": "PostgreSQL FluxHub - itens de pedidos pagos",
            "aviso": "Os valores não descontam taxas, frete, estornos ou custos; dados podem estar incompletos.",
        }

    days = _number(args, "dias", 30, 365)
    since = date.today() - timedelta(days=days)
    count, spend, attributed, updated = db.execute(select(
        func.count(Ads.id), func.coalesce(func.sum(Ads.ad_spend), 0),
        func.coalesce(func.sum(Ads.attributed_revenue), 0),
        func.max(Ads.captured_at),
    ).where(Ads.user_id == user_id, Ads.report_date >= since)).one()
    return {
        "periodo_dias": days,
        "registros_ads": int(count or 0),
        "gasto_publicidade_brl": round(float(spend or 0), 2) if count else None,
        "receita_atribuida_ads_brl": round(float(attributed or 0), 2) if count else None,
        "atualizado_em": updated.isoformat() if updated else None,
        "origem": "PostgreSQL FluxHub - snapshots Product Ads",
        "aviso": "Sem registros não significa gasto zero. Receita atribuída não é lucro.",
    }
