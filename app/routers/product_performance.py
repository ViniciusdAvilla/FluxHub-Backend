"""Individual Mercado Livre listing metrics with tenant isolation and honest data provenance."""
from datetime import date, timedelta, timezone, datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    UserModel, MercadoLivreListingSnapshotModel, MercadoLivreAdsDailySnapshotModel,
    MercadoLivreProductCostModel,
)
from app.database.session import get_db
from app.routers.mercado_livre import get_account, seller_request

router = APIRouter(prefix="/analytics/items", tags=["Analytics"])
ITEM_ID = re.compile(r"^ML[A-Z]{1,2}[0-9]{5,20}$")


def linked_account(db, user_id, item_id, account_id):
    if account_id is not None:
        return get_account(db, user_id, account_id)
    matches = db.scalars(select(MercadoLivreListingSnapshotModel).where(
        MercadoLivreListingSnapshotModel.user_id == user_id,
        MercadoLivreListingSnapshotModel.external_id == item_id)).all()
    ids = {r.mercado_livre_user_id for r in matches if r.mercado_livre_user_id}
    if len(ids) == 1:
        return get_account(db, user_id, ids.pop())
    return get_account(db, user_id, None)


@router.get("/{item_id}/performance")
async def product_performance(
    item_id: str,
    account_id: int | None = Query(default=None, ge=1),
    days: int = Query(default=30),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not ITEM_ID.fullmatch(item_id):
        raise HTTPException(422, "Identificador de anúncio inválido.")
    if days not in (7, 30, 90):
        raise HTTPException(422, "Período permitido: 7, 30 ou 90 dias.")
    account = linked_account(db, user.id, item_id, account_id)
    seller_id = account.mercado_livre_user_id

    item = await seller_request(db, account, f"/items/{item_id}")
    if not isinstance(item, dict) or int(item.get("seller_id") or 0) != seller_id:
        raise HTTPException(404, "Anúncio não pertence à conta conectada.")

    visits = None
    visits_daily = []
    try:
        data = await seller_request(db, account,
                                    f"/items/{item_id}/visits/time_window",
                                    {"last": days, "unit": "day"})
        if isinstance(data, dict) and isinstance(data.get("total_visits"), int):
            visits = data["total_visits"]
            visits_daily = [
                {"date": str(row["date"])[:10], "visits": int(row["total"])}
                for row in data.get("results", [])
                if isinstance(row, dict) and row.get("date") and
                isinstance(row.get("total"), int) and row["total"] >= 0
            ]
    except HTTPException:
        pass

    quality = None
    try:
        quality_data = await seller_request(db, account, f"/item/{item_id}/performance")
        if isinstance(quality_data, dict):
            quality = {
                "score": quality_data.get("score"),
                "level": quality_data.get("level_wording") or quality_data.get("level"),
                "opportunities": [
                    {"title": bucket.get("title"), "status": bucket.get("status")}
                    for bucket in quality_data.get("buckets", [])
                    if isinstance(bucket, dict) and bucket.get("status") == "PENDING"
                ][:8],
            }
    except HTTPException:
        pass

    since = date.today() - timedelta(days=days)
    ads_rows = db.scalars(select(MercadoLivreAdsDailySnapshotModel).where(
        MercadoLivreAdsDailySnapshotModel.user_id == user.id,
        MercadoLivreAdsDailySnapshotModel.mercado_livre_user_id == seller_id,
        MercadoLivreAdsDailySnapshotModel.external_id == item_id,
        MercadoLivreAdsDailySnapshotModel.report_date >= since)).all()
    ads = None
    if ads_rows:
        ads = {
            "source": "manual_ads_report",
            "attributed_sales": sum(row.attributed_sales for row in ads_rows),
            "attributed_revenue": round(sum(row.attributed_revenue for row in ads_rows), 2),
            "ad_spend": round(sum(row.ad_spend for row in ads_rows), 2),
        }

    cost = db.scalar(select(MercadoLivreProductCostModel).where(
        MercadoLivreProductCostModel.user_id == user.id,
        MercadoLivreProductCostModel.external_id == item_id))
    unit_margin = None
    if cost is not None and cost.marketplace_fee_percent is not None:
        price = float(item.get("price") or 0)
        if price > 0:
            unit_profit = (price * (1 - (cost.marketplace_fee_percent +
                       cost.tax_percent) / 100) - cost.unit_cost -
                       cost.shipping_cost - cost.other_cost)
            unit_margin = {
                "estimated_unit_profit": round(unit_profit, 2),
                "estimated_margin_percent": round(unit_profit / price * 100, 2),
                "source": "user_entered_costs",
            }
    pictures = item.get("pictures") or []
    thumbnail = item.get("thumbnail")
    if pictures and isinstance(pictures[0], dict):
        thumbnail = pictures[0].get("secure_url") or pictures[0].get("url") or thumbnail
    return {
        "item": {
            "id": item_id,
            "account_id": seller_id,
            "title": item.get("title"),
            "status": item.get("status"),
            "price": item.get("price"),
            "currency_id": item.get("currency_id"),
            "stock": item.get("available_quantity"),
            "sold_lifetime": item.get("sold_quantity"),
            "category_id": item.get("category_id"),
            "thumbnail": thumbnail,
            "permalink": item.get("permalink"),
        },
        "days": days,
        "visits": {
            "total": visits,
            "daily": sorted(visits_daily, key=lambda row: row["date"]),
            "source": "mercado_livre_api" if visits is not None else "unavailable",
        },
        "quality": quality,
        "ads": ads,
        "unit_margin": unit_margin,
        "sales_period": None,
        "conversion_period": None,
        "revenue_period": None,
        "note": "Vendas totais, conversão e receita do período não são inferidas do acumulado vitalício.",
    }
