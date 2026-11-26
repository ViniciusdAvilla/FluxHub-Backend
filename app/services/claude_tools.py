"""Read-only, tenant-scoped tools shared by Claude chat and the remote MCP server.

Every query independently enforces user_id. No buyer PII or OAuth secrets are
ever returned. All values are taken from persisted snapshots and may be partial.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.report_period import REPORT_ZONE, timestamp_filters, validate_period
from app.database.models import (
    MercadoLivreAdsDailySnapshotModel as Ads,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderLineModel as Line,
    MercadoLivreOrderSnapshotModel as Order,
    ProductModel as Product,
    MercadoLivreProductCostModel as Cost,
    MercadoLivreVisitDailySnapshotModel as Visit,
    CompanyDataSourceModel as Source,
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
        "name": "resumo_product_ads",
        "description": "Gastos e receita atribuída a Product Ads nos snapshots disponíveis. Pode haver dados ausentes.",
        "input_schema": {"type": "object", "properties": {
            "dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30}
        }, "additionalProperties": False},
    },
]

def _definition(name: str, description: str, properties: dict) -> dict:
    return {"name": name, "description": description, "input_schema": {
        "type": "object", "properties": properties, "additionalProperties": False,
    }}


_search = {"type": "string", "minLength": 1, "maxLength": 100}
_limit = {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}
_dates = {
    "dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
    "inicio": {"type": "string", "format": "date", "description": "Data inicial inclusiva AAAA-MM-DD."},
    "fim": {"type": "string", "format": "date", "description": "Data final inclusiva AAAA-MM-DD."},
    "periodo": {"type": "string", "enum": ["hoje", "ontem", "este_mes", "mes_passado"]},
}
_metrics = {
    "pedidos": ("pedidos_registrados",), "pedidos_pagos": ("pedidos_pagos",),
    "cancelamentos": ("pedidos_cancelados",), "faturamento": ("faturamento_pago_bruto_brl",),
    "visitas": ("visitas", "dias_com_visitas"),
    "ads": ("gasto_ads_brl", "receita_atribuida_ads_brl", "dias_com_ads"),
    "resumo": (),
}
_listing_statuses = ("active", "paused", "under_review", "closed", "inactive", "pending", "deleted")
TOOL_DEFINITIONS.extend([
    _definition("produtos_mais_vendidos", "Unidades e faturamento bruto de itens de pedidos pagos. Para um produto específico informe o ID exato ou termos do nome. Sem filtro, retorna o ranking.", {
        **_dates, "limite": _limit, "id_anuncio": _search, "busca": _search,
        "ordenar_por": {"type": "string", "enum": ["unidades", "faturamento"]},
    }),
    _definition("consultar_estoque", "Produtos com estoque baixo. Use estoque_maximo=0 para esgotados ou informe o limite pedido. A contagem cobre todos os resultados.", {
        "limite": _limit, "estoque_maximo": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 5},
    }),
    _definition("consultar_catalogo", "Pesquisa no catálogo completo da conta por ID de anúncio, nome, categoria ou fabricante. Retorna preço, estoque e custos cadastrados por produto com paginação.", {
        "busca": _search, "limite": _limit, "pagina": {"type": "integer", "minimum": 1, "maximum": 10000},
    }),
    _definition("contar_anuncios", "Conta anúncios armazenados no FluxHub e mostra os totais por status. Informe status para responder sobre um status específico.", {
        "status": {"type": "string", "enum": list(_listing_statuses)},
    }),
    _definition("diagnosticar_dados", "Cobertura dos dados do FluxHub: integrações, última sincronização, anúncios por status, intervalo de pedidos e quantidade de custos cadastrados.", {}),
    _definition("desempenho_periodo", "Consulta uma métrica em um intervalo. Informe metrica quando a pergunta pedir um indicador específico. Para comparar, chame a ferramenta para os dois intervalos.", {
        **_dates, "metrica": {"type": "string", "enum": list(_metrics)},
    }),
])

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

def _catalog(db: Session, user_id: int, args: dict) -> dict:
    limit = _number(args, "limite", 10, 20)
    page = _number(args, "pagina", 1, 10000)
    search = args.get("busca")
    filters = [Product.company_id == user_id]
    if search is not None:
        if not isinstance(search, str) or not 1 <= len(search.strip()) <= 100:
            raise ValueError("Informe uma busca de 1 a 100 caracteres.")
        term = search.strip().lower()
        filters.append(or_(*(func.lower(field).contains(term, autoescape=True) for field in (
            Product.external_id, Product.title, Product.category, Product.brand, Cost.manufacturer,
        ))))
    link = and_(Cost.user_id == user_id, Cost.external_id == Product.external_id)
    total = db.scalar(select(func.count(Product.id)).outerjoin(Cost, link).where(*filters)) or 0
    rows = db.execute(select(
        Product.external_id.label("id_anuncio"), Product.source_key.label("conta"),
        Product.title.label("produto"), Product.category.label("categoria_id"),
        Product.brand.label("marca"), Cost.manufacturer.label("fabricante"),
        Product.original_price.label("preco_original_brl"), Product.promotional_price.label("preco_promocional_brl"),
        Product.stock_qty.label("estoque"), Cost.unit_cost.label("custo_unitario_brl"),
        Cost.marketplace_fee_percent.label("taxa_marketplace_pct"), Cost.tax_percent.label("imposto_pct"),
        Cost.shipping_cost.label("frete_brl"), Cost.other_cost.label("outros_custos_brl"),
    ).outerjoin(Cost, link).where(*filters).order_by(Product.title, Product.id)
        .offset((page - 1) * limit).limit(limit)).mappings()
    return {"origem": "PostgreSQL FluxHub — catálogo e custos cadastrados", "total_encontrado": total,
            "pagina": page, "limite": limit, "produtos": [dict(row) for row in rows],
            "aviso": "Custos cadastrados são estimativas para precificação. Nulo significa que o dado não está disponível."}


def _listing_counts(db: Session, user_id: int, status: str | None = None) -> dict:
    if status is not None and status not in _listing_statuses:
        raise ValueError("Status do anúncio inválido.")
    filters = [Listing.user_id == user_id]
    if status is not None:
        filters.append(Listing.status == status)
    counts = dict(db.execute(select(Listing.status, func.count(Listing.id)).where(
        *filters).group_by(Listing.status)).all())
    if status is not None:
        counts.setdefault(status, 0)
    total_field = "total_no_status" if status is not None else "total_anuncios"
    return {total_field: sum(counts.values()), "anuncios_por_status": counts,
            "origem": "PostgreSQL FluxHub — anúncios sincronizados",
            "aviso": "Contagem dos anúncios armazenados; a cobertura depende da sincronização."}


def _coverage(db: Session, user_id: int) -> dict:
    sources = db.execute(select(
        Source.provider.label("provedor"), Source.display_name.label("nome"),
        Source.connected.label("conectado"), Source.enabled.label("ativo"),
        Source.last_synced_at.label("sincronizado_em"),
    ).where(Source.company_id == user_id).order_by(Source.id).limit(20)).mappings()
    start, end = db.execute(select(func.min(Order.created_at), func.max(Order.created_at))
                            .where(Order.user_id == user_id)).one()
    listings = _listing_counts(db, user_id)
    costs = db.scalar(select(func.count(Cost.id)).where(Cost.user_id == user_id)) or 0
    return {"origem": "PostgreSQL FluxHub — fontes, anúncios, pedidos e custos",
            "integracoes": [{**row, "sincronizado_em": row["sincronizado_em"].isoformat() if row["sincronizado_em"] else None}
                            for row in sources], "anuncios_por_status": listings["anuncios_por_status"],
            "primeiro_pedido_registrado": start.isoformat() if start else None,
            "ultimo_pedido_registrado": end.isoformat() if end else None,
            "custos_cadastrados": costs,
            "aviso": "Os extremos do histórico não garantem que todos os dias foram sincronizados."}


def _period(args: dict) -> tuple[date, date]:
    today = datetime.now(REPORT_ZONE).date()
    preset = args.get("periodo")
    date_range = "inicio" in args or "fim" in args
    if sum((date_range, "dias" in args, "periodo" in args)) > 1:
        raise ValueError("Escolha apenas um formato de período.")
    try:
        if date_range:
            start = date.fromisoformat(args["inicio"])
            end = date.fromisoformat(args["fim"])
        elif preset == "hoje":
            start = end = today
        elif preset == "ontem":
            start = end = today - timedelta(days=1)
        elif preset == "este_mes":
            start, end = today.replace(day=1), today
        elif preset == "mes_passado":
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        elif "periodo" in args:
            raise ValueError
        else:
            end = today
            start = end - timedelta(days=_number(args, "dias", 30, 365) - 1)
        validate_period(start, end)
    except (KeyError, TypeError, ValueError, HTTPException) as error:
        raise ValueError("Informe um período válido, com até 365 dias e data final até hoje.") from error
    return start, end


def _performance(db: Session, user_id: int, args: dict) -> dict:
    metric = args.get("metrica", "resumo")
    if not isinstance(metric, str) or metric not in _metrics:
        raise ValueError("Métrica inválida.")
    start, end = _period(args)
    data = {}
    if metric in ("resumo", "pedidos", "pedidos_pagos", "cancelamentos", "faturamento"):
        data.update(_performance_orders(db, user_id, start, end))
    if metric in ("resumo", "visitas"):
        visit_days, visits = db.execute(select(func.count(func.distinct(Visit.report_date)), func.sum(Visit.total_visits))
            .where(Visit.user_id == user_id, Visit.report_date.between(start, end))).one()
        data.update(visitas=visits, dias_com_visitas=visit_days)
    if metric in ("resumo", "ads"):
        ads_days, spend, attributed = db.execute(select(func.count(func.distinct(Ads.report_date)),
            func.sum(Ads.ad_spend), func.sum(Ads.attributed_revenue))
            .where(Ads.user_id == user_id, Ads.report_date.between(start, end))).one()
        data.update(gasto_ads_brl=round(spend, 2) if spend is not None else None,
                    receita_atribuida_ads_brl=round(attributed, 2) if attributed is not None else None,
                    dias_com_ads=ads_days)
    fields = _metrics[metric] or tuple(data)
    return {
        **{field: data[field] for field in fields},
        "inicio": start.isoformat(), "fim": end.isoformat(), "fuso": "America/Sao_Paulo",
        "origem": "PostgreSQL FluxHub — dados sincronizados",
        "aviso": "O histórico sincronizado pode estar parcial. Valores de faturamento são brutos.",
    }


def _performance_orders(db: Session, user_id: int, start: date, end: date) -> dict:
    filters = timestamp_filters(Order.created_at, None, start, end)
    paid = and_(Order.status == "paid", Order.is_cancelled.is_(False))
    orders, count_paid, cancelled, gross = db.execute(select(
        func.count(Order.id), func.count(Order.id).filter(paid),
        func.count(Order.id).filter(Order.is_cancelled.is_(True)),
        func.sum(Order.total_amount).filter(paid),
    ).where(Order.user_id == user_id, *filters)).one()
    return {"pedidos_registrados": orders, "pedidos_pagos": count_paid, "pedidos_cancelados": cancelled,
            "faturamento_pago_bruto_brl": round(gross, 2) if gross is not None else None}


def execute_tool(db: Session, user_id: int, name: str, arguments: dict | None = None) -> dict:
    if name not in {entry["name"] for entry in TOOL_DEFINITIONS}:
        raise ValueError("Ferramenta não autorizada.")
    args = arguments if isinstance(arguments, dict) else {}
    allowed = next(item for item in TOOL_DEFINITIONS if item["name"] == name)
    if set(args) - set(allowed["input_schema"]["properties"]):
        raise ValueError("Parâmetros não reconhecidos.")

    if name == "consultar_catalogo":
        return _catalog(db, user_id, args)
    if name == "contar_anuncios":
        return _listing_counts(db, user_id, args.get("status"))
    if name == "diagnosticar_dados":
        return _coverage(db, user_id)
    if name == "desempenho_periodo":
        return _performance(db, user_id, args)

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
        maximum = args.get("estoque_maximo", 5)
        if type(maximum) is not int or not 0 <= maximum <= 10000:
            raise ValueError("Limite de estoque inválido.")
        stock_conditions = (Product.company_id == user_id, Product.stock_qty <= maximum)
        total = db.scalar(select(func.count(Product.id)).where(*stock_conditions))
        rows = db.execute(select(
            Product.external_id, Product.title, Product.stock_qty,
        ).where(*stock_conditions).order_by(Product.stock_qty.asc(), Product.title.asc())
                          .limit(limit)).all()
        return {
            "origem": "PostgreSQL FluxHub - catálogo armazenado",
            "estoque_baixo_ate": maximum,
            "total_encontrado": total,
            "produtos": [{"id": identifier, "nome": title, "quantidade": qty}
                         for identifier, title, qty in rows],
            "aviso": "Quantidade corresponde à última sincronização; não é estoque em tempo real.",
        }

    if name == "produtos_mais_vendidos":
        start, end = _period(args)
        limit = _number(args, "limite", 10, 20)
        order_by = args.get("ordenar_por", "unidades")
        if order_by not in ("unidades", "faturamento"):
            raise ValueError("Ordenação inválida.")
        item_id = args.get("id_anuncio")
        if item_id is not None and (not isinstance(item_id, str) or not 1 <= len(item_id.strip()) <= 100):
            raise ValueError("Informe um ID de anúncio válido.")
        search = args.get("busca")
        if search is not None and (not isinstance(search, str) or not 1 <= len(search.strip()) <= 100):
            raise ValueError("Informe termos válidos para o produto.")
        item_scope = []
        if item_id is not None:
            item_scope.append(func.upper(Line.item_id) == item_id.strip().upper())
        if search is not None:
            item_scope.extend(func.lower(Line.title).contains(token, autoescape=True) for token in search.lower().split())
        units = func.sum(Line.quantity).label("unidades")
        gross = func.sum(Line.quantity * Line.unit_price).label("bruto")
        ordering = (gross, units) if order_by == "faturamento" else (units, gross)
        rows = db.execute(select(
            Line.item_id, func.max(Line.title),
            units, gross,
        ).join(Order, and_(
            Order.user_id == Line.user_id,
            Order.mercado_livre_user_id == Line.mercado_livre_user_id,
            Order.external_id == Line.order_external_id,
        )).where(
            Order.user_id == user_id,
            Line.user_id == user_id,
            *timestamp_filters(Order.created_at, None, start, end),
            *item_scope,
            Order.status == "paid",
            Order.is_cancelled.is_(False),
        ).group_by(Line.item_id).order_by(
            *(column.desc() for column in ordering), Line.item_id,
        ).limit(limit)).all()
        return {
            "inicio": start.isoformat(),
            "fim": end.isoformat(),
            "periodo_dias": (end - start).days + 1,
            "produtos": [{"id": item_id, "nome": title, "unidades": int(units),
                          "vendas_brutas_brl": round(float(gross), 2)}
                         for item_id, title, units, gross in rows],
            "origem": "PostgreSQL FluxHub - itens de pedidos pagos",
            **({"produto_consultado": item_id or search} if item_id or search else {}),
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
