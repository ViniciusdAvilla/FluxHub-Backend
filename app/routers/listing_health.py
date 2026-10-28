"""Tenant-scoped listing profit health using only configured, traceable costs."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    UserModel,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreProductCostModel as Cost,
)
from app.database.session import get_db

router = APIRouter(prefix="/listing-health", tags=["Listing Health"])


@router.get("/")
def listing_health(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default="", max_length=100),
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(
        Listing.external_id, Listing.mercado_livre_user_id,
        Listing.title, Listing.price, Listing.status,
        Cost.unit_cost, Cost.marketplace_fee_percent,
        Cost.tax_percent, Cost.shipping_cost, Cost.other_cost,
    ).outerjoin(Cost, and_(
        Cost.user_id == Listing.user_id, Cost.external_id == Listing.external_id,
    )).where(Listing.user_id == user.id)
    if account_id is not None:
        stmt = stmt.where(Listing.mercado_livre_user_id == account_id)
    if search.strip():
        pattern = "%" + search.strip() + "%"
        stmt = stmt.where(or_(
            Listing.title.ilike(pattern), Listing.external_id.ilike(pattern)))
    stmt = stmt.order_by(Listing.external_id, Listing.mercado_livre_user_id)
    counts = {"healthy": 0, "attention": 0, "loss": 0, "incomplete": 0}
    items = []
    scanned = 0
    for record in db.execute(stmt).yield_per(500):
        item_id, seller_id, title, price, listing_status, unit_cost, rate, tax, shipping, other = record
        if unit_cost is None or rate is None or price is None or price <= 0:
            health = {"status": "incomplete", "profit": None, "margin": None}
        else:
            fee = price * rate / 100
            profit = round(price - fee - price * (tax or 0) / 100 -
                           unit_cost - (shipping or 0) - (other or 0), 2)
            margin = round(100 * profit / price, 2)
            health = {"status": "loss" if profit < 0 else
                      "attention" if margin < 10 else "healthy",
                      "profit": profit, "margin": margin}
        counts[health["status"]] += 1
        if offset <= scanned < offset + limit:
            items.append({
                "item_id": item_id, "account_id": seller_id, "title": title,
                "listing_status": listing_status, "price": price,
                "configured_unit_cost": unit_cost,
                "fee_source": "configured_percentage" if rate is not None else "missing",
                "health": health["status"], "profit": health["profit"],
                "margin": health["margin"],
            })
        scanned += 1
    return {
        "total": scanned, "limit": limit, "offset": offset,
        "counts": counts, "items": items,
        "basis": "configured_cost_and_fee_estimate",
        "note": ("A saúde financeira é uma estimativa com os custos e percentuais "
                 "configurados. Sem custo ou comissão fica pendente. "
                 "Frete real, devoluções, impostos por UF, tarifas cobradas "
                 "e liquidação não estão conciliados neste indicador."),
    }
