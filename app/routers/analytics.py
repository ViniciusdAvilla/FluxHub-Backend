"""Snapshot-based seller analytics. Never fabricate marketplace metrics."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import (
    UserModel, CompanyDataSourceModel, ProductModel,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreProductCostModel as Cost,
    MercadoLivreAdsDailySnapshotModel as Ads,
)
from app.database.session import get_db
from app.routers.mercado_livre import get_account, seller_request

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class CostInput(BaseModel):
    unit_cost: float = Field(ge=0, le=1_000_000_000)
    marketplace_fee_percent: float | None = Field(default=None, ge=0, le=100)
    manufacturer: str = Field(default="", max_length=120)
    tax_percent: float = Field(default=0, ge=0, le=100)
    shipping_cost: float = Field(default=0, ge=0)
    other_cost: float = Field(default=0, ge=0)


class CostRow(CostInput):
    external_id: str = Field(min_length=3, max_length=100)


class CostImport(BaseModel):
    items: list[CostRow] = Field(min_length=1, max_length=500)


class AdsRow(BaseModel):
    account_id: int
    external_id: str = Field(min_length=3, max_length=100)
    report_date: date
    attributed_sales: int = Field(ge=0)
    ad_spend: float = Field(ge=0)
    attributed_revenue: float = Field(ge=0)


class AdsImport(BaseModel):
    items: list[AdsRow] = Field(min_length=1, max_length=500)


def now():
    return datetime.now(timezone.utc)


def parse_date(value):
    if not value:
        return now()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(502, "Data inválida recebida do marketplace.")


def store_cost(db, user_id, external_id, body):
    cost = db.scalar(select(Cost).where(
        Cost.user_id == user_id, Cost.external_id == external_id))
    if cost is None:
        cost = Cost(user_id=user_id, external_id=external_id)
        db.add(cost)
    for key, value in body.model_dump(exclude={"external_id"}).items():
        setattr(cost, key, value)
    return cost


@router.put("/product-costs/{external_id}")
def update_cost(external_id: str, body: CostInput,
                user: UserModel = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if not (3 <= len(external_id) <= 100):
        raise HTTPException(422, "Identificador inválido.")
    store_cost(db, user.id, external_id, body)
    db.commit()
    return {"updated": True, "external_id": external_id}


@router.post("/product-costs/import")
def import_costs(body: CostImport,
                 user: UserModel = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    for row in body.items:
        store_cost(db, user.id, row.external_id, row)
    db.commit()
    return {"imported": len(body.items)}


@router.get("/product-costs")
def list_costs(user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    rows = db.scalars(select(Cost).where(Cost.user_id == user.id)
                      .order_by(Cost.external_id).limit(1000)).all()
    return [{"external_id": c.external_id, "unit_cost": c.unit_cost,
             "manufacturer": c.manufacturer,
             "marketplace_fee_percent": c.marketplace_fee_percent,
             "tax_percent": c.tax_percent, "shipping_cost": c.shipping_cost,
             "other_cost": c.other_cost} for c in rows]


@router.post("/ads-daily/import")
def import_ads_reports(body: AdsImport, user: UserModel = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    # This endpoint imports already-exported Ads data. It does NOT claim API sync.
    authorized = {r[0] for r in db.execute(
        select(__import__("app.database.models", fromlist=["MercadoLivreConnectionModel"])
               .MercadoLivreConnectionModel.mercado_livre_user_id)
        .where(__import__("app.database.models", fromlist=["MercadoLivreConnectionModel"])
               .MercadoLivreConnectionModel.user_id == user.id)).all()}
    if any(row.account_id not in authorized for row in body.items):
        raise HTTPException(403, "Conta Mercado Livre não pertence ao usuário.")
    for row in body.items:
        entry = db.scalar(select(Ads).where(
            Ads.user_id == user.id, Ads.mercado_livre_user_id == row.account_id,
            Ads.external_id == row.external_id, Ads.report_date == row.report_date))
        if entry is None:
            entry = Ads(user_id=user.id, mercado_livre_user_id=row.account_id,
                        external_id=row.external_id, report_date=row.report_date)
            db.add(entry)
        entry.attributed_sales = row.attributed_sales
        entry.ad_spend = row.ad_spend
        entry.attributed_revenue = row.attributed_revenue
    db.commit()
    return {"imported": len(body.items), "source": "manual_ads_report"}


@router.post("/sync")
async def sync(account_id: int | None = None,
               offset: int = Query(default=0, ge=0, le=1000),
               limit: int = Query(default=50, ge=1, le=50),
               user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    account = get_account(db, user.id, account_id)
    seller_id = account.mercado_livre_user_id
    source_key = f"ml:{seller_id}"
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.account_key == source_key))
    if source is None or not source.connected:
        raise HTTPException(409, "Fonte Mercado Livre não conectada.")

    page = await seller_request(db, account, f"/users/{seller_id}/items/search",
                                {"limit": limit, "offset": offset})
    ids = page.get("results", [])
    if not isinstance(ids, list):
        raise HTTPException(502, "Lista de anúncios inválida.")
    ids = [item for item in ids if isinstance(item, str) and item.isalnum()][:limit]
    details = []
    if ids:
        data = await seller_request(db, account, "/items",
                                    {"ids": ",".join(ids)})
        if not isinstance(data, list):
            raise HTTPException(502, "Detalhes de anúncios inválidos.")
        details = [row.get("body") for row in data if isinstance(row, dict)
                   and row.get("code") == 200 and isinstance(row.get("body"), dict)]

    imported = 0
    for item in details:
        if int(item.get("seller_id") or 0) != seller_id:
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        listing = db.scalar(select(Listing).where(
            Listing.user_id == user.id, Listing.mercado_livre_user_id == seller_id,
            Listing.external_id == item_id))
        if listing is None:
            listing = Listing(user_id=user.id, mercado_livre_user_id=seller_id,
                              external_id=item_id)
            db.add(listing)
        listing.title = str(item.get("title") or "")[:255]
        listing.category_id = item.get("category_id")
        listing.status = str(item.get("status") or "unknown")[:40]
        listing.listing_type_id = item.get("listing_type_id")
        listing.price = float(item.get("price") or 0)
        listing.currency_id = item.get("currency_id")
        listing.published_at = parse_date(item["date_created"]) if item.get("date_created") else None
        listing.captured_at = now()
        # No synthetic 30-day visits or orders; use real Ads reports separately.
        product = db.scalar(select(ProductModel).where(
            ProductModel.company_id == user.id,
            ProductModel.source_key == source_key,
            ProductModel.external_id == item_id))
        if product is None:
            product = ProductModel(company_id=user.id, source_key=source_key,
                                   external_id=item_id, category="", brand="")
            db.add(product)
        product.title = listing.title
        product.category = str(item.get("category_id") or "Sem categoria")
        brand = next((a.get("value_name") for a in item.get("attributes", [])
                      if isinstance(a, dict) and a.get("id") == "BRAND"), None)
        product.brand = str(brand or "Sem marca")[:100]
        product.original_price = float(item.get("original_price") or item.get("price") or 0)
        product.promotional_price = float(item.get("price") or 0)
        product.discount_rate = (
            round(100 * (product.original_price - product.promotional_price) /
                  product.original_price, 2) if product.original_price > 0 else 0)
        product.stock_qty = item.get("available_quantity")
        product.sales_30d = None
        product.rating = None
        product.is_free_shipping = bool((item.get("shipping") or {}).get("free_shipping", False))
        product.item_condition = str(item.get("condition") or "unknown")[:50]
        imported += 1

    # Orders are independently paginated; do not assert this captures all history.
    orders_page = await seller_request(db, account, "/orders/search", {
        "seller": seller_id, "limit": limit, "offset": offset,
        "sort": "date_desc"})
    saved_orders = 0
    for item in orders_page.get("results", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        seller = item.get("seller") or {}
        if int(seller.get("id") or 0) != seller_id:
            continue
        external_id = str(item["id"])
        row = db.scalar(select(Order).where(
            Order.user_id == user.id, Order.mercado_livre_user_id == seller_id,
            Order.external_id == external_id))
        if row is None:
            row = Order(user_id=user.id, mercado_livre_user_id=seller_id,
                        external_id=external_id)
            db.add(row)
        row.status = str(item.get("status") or "")[:40]
        row.total_amount = float(item.get("total_amount") or 0)
        row.created_at = parse_date(item.get("date_created"))
        row.is_cancelled = row.status == "cancelled"
        saved_orders += 1
    source.last_synced_at = now()
    source.status_message = f"{imported} anúncios e {saved_orders} pedidos nesta página."
    db.commit()
    return {"source": source_key, "listings": imported,
            "orders": saved_orders, "offset": offset,
            "limit": limit, "next_offset": offset + limit if len(ids) == limit else None,
            "partial": True, "ads_data_synced": False}


@router.get("/ads-performance")
def ads_performance(user: UserModel = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    rows = db.scalars(select(Listing).where(Listing.user_id == user.id)).all()
    statuses = defaultdict(int)
    for row in rows:
        statuses[row.status] += 1
    return {"total_listings": len(rows), "by_status": dict(statuses),
            "active_listings": statuses.get("active", 0),
            "average_price": round(sum(r.price for r in rows) / len(rows), 2) if rows else None,
            "visits_30d": None, "visits_data_available": False,
            "low_relevance_listings": None,
            "note": "Visitas e vendas atribuídas não são inferidas sem relatório real."}


@router.get("/cancellations")
def cancellations(days: int = Query(default=30, ge=1, le=365),
                  user: UserModel = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    rows = db.scalars(select(Order).where(
        Order.user_id == user.id, Order.created_at >= now() - timedelta(days=days))).all()
    canceled = [r for r in rows if r.is_cancelled]
    return {"days": days, "total_orders": len(rows),
            "cancelled_orders": len(canceled),
            "cancellation_rate_percent": round(100 * len(canceled) / len(rows), 2) if rows else None,
            "canceled_order_value": round(sum(r.total_amount for r in canceled), 2),
            "listing_cancellations": None}


@router.get("/financial-monitoring")
def financial(days: int = Query(default=30, ge=1, le=365),
              user: UserModel = Depends(get_current_user),
              db: Session = Depends(get_db)):
    # Active listing value is asking price sum, NOT inventory cost or revenue.
    rows = db.scalars(select(Listing).where(Listing.user_id == user.id)).all()
    active = [row for row in rows if row.status == "active"]
    by_category = defaultdict(list)
    for row in active:
        if row.category_id:
            by_category[row.category_id].append(row.price)
    over = []
    under = []
    for row in active:
        peers = by_category.get(row.category_id, [])
        if len(peers) < 3:
            continue
        avg = sum(peers) / len(peers)
        if row.price > 1.2 * avg:
            over.append(row.external_id)
        elif row.price < 0.8 * avg:
            under.append(row.external_id)
    canceled_orders = db.scalars(select(Order).where(
        Order.user_id == user.id, Order.is_cancelled.is_(True),
        Order.created_at >= now() - timedelta(days=days))).all()
    return {"days": days, "active_listings": len(active),
            "active_listing_asking_price_sum": round(sum(r.price for r in active), 2),
            "above_category_average_20pct": over,
            "below_category_average_20pct": under,
            "canceled_order_value": round(sum(o.total_amount for o in canceled_orders), 2),
            "note": "Comparações heurísticas entre anúncios cadastrados, não previsão de vendas."}


@router.get("/ads-profitability")
def profitability(days: int = Query(default=30, ge=1, le=365),
                  user: UserModel = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    cutoff = date.today() - timedelta(days=days)
    rows = db.scalars(select(Ads).where(
        Ads.user_id == user.id, Ads.report_date >= cutoff)).all()
    costs = {r.external_id: r for r in db.scalars(
        select(Cost).where(Cost.user_id == user.id)).all()}
    spend = sum(r.ad_spend for r in rows)
    revenue = sum(r.attributed_revenue for r in rows)
    attributable_profit = 0.0
    incomplete = []
    for row in rows:
        c = costs.get(row.external_id)
        if c is None or c.marketplace_fee_percent is None:
            incomplete.append(row.external_id)
            continue
        item_margin = (row.attributed_revenue * (
            1 - (c.marketplace_fee_percent + c.tax_percent) / 100)
            - row.attributed_sales * (
                c.unit_cost + c.shipping_cost + c.other_cost))
        attributable_profit += item_margin
    is_complete = bool(rows) and not incomplete
    return {"days": days, "has_ads_data": bool(rows),
            "ad_spend": round(spend, 2) if rows else None,
            "attributed_revenue": round(revenue, 2) if rows else None,
            "roas": round(revenue / spend, 4) if spend else None,
            "net_profit_after_ads": round(attributable_profit - spend, 2)
            if is_complete else None,
            "real_roas": round((attributable_profit - spend) / spend, 4)
            if is_complete and spend else None,
            "incomplete_external_ids": sorted(set(incomplete)),
            "profit_complete": is_complete}
