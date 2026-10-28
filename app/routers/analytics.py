"""Snapshot-based seller analytics. Never fabricate marketplace metrics."""
from collections import defaultdict
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import (
    UserModel, CompanyDataSourceModel, ProductModel, MercadoLivreConnectionModel,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreProductCostModel as Cost,
    MercadoLivreAdsDailySnapshotModel as Ads,
    MercadoLivreVisitDailySnapshotModel as Visits,
    MercadoLivreOrderLineModel as OrderLine,
    MercadoLivreListingPhotoModel as ListingPhoto,
    MercadoLivreBuyerOrderModel as BuyerOrder,
    MercadoLivreSyncJobModel as SyncJob,
)
from app.database.session import get_db
from app.routers.mercado_livre import get_account, seller_request

logger = logging.getLogger(__name__)
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


def safe_public_thumbnail(value):
    """Only permit public ML CDN images, never arbitrary injected hosts."""
    if not isinstance(value, str) or len(value) > 1000:
        return None
    uri = urlparse(value)
    host = (uri.hostname or "").lower()
    if uri.scheme == "https" and (host == "mlstatic.com" or host.endswith(".mlstatic.com")):
        return value
    return None


def thumbnail_from_item(item):
    images = item.get("pictures")
    first = images[0] if isinstance(images, list) and images else {}
    if not isinstance(first, dict):
        first = {}
    for candidate in (item.get("secure_thumbnail"), item.get("thumbnail"),
                      first.get("secure_url"), first.get("url")):
        safe = safe_public_thumbnail(candidate)
        if safe:
            return safe
    return None


def optional_money(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


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
def list_costs(limit: int = Query(default=500, ge=1, le=1000),
               offset: int = Query(default=0, ge=0),
               user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    rows = db.scalars(select(Cost).where(Cost.user_id == user.id)
                      .order_by(Cost.external_id).offset(offset).limit(limit)).all()
    return [{"external_id": c.external_id, "unit_cost": c.unit_cost,
             "manufacturer": c.manufacturer,
             "marketplace_fee_percent": c.marketplace_fee_percent,
             "tax_percent": c.tax_percent, "shipping_cost": c.shipping_cost,
             "other_cost": c.other_cost} for c in rows]




@router.get("/catalog-metrics")
def catalog_metrics(
    days: int = Query(default=30, ge=1, le=365),
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Real paid units, saved costs and *reported* Ads by item for the catalog.

    The listing sync cannot supply sales_30d: quantities come from order lines,
    and Product Ads is a separate source. Never substitute missing metrics with 0.
    """
    connected_accounts = set(db.scalars(
        select(MercadoLivreConnectionModel.mercado_livre_user_id).where(
            MercadoLivreConnectionModel.user_id == user.id)
    ).all())
    if account_id is not None and account_id not in connected_accounts:
        raise HTTPException(403, "Conta Mercado Livre não pertence ao usuário.")

    cutoff = now() - timedelta(days=days)
    line_match = and_(
        OrderLine.user_id == Order.user_id,
        OrderLine.mercado_livre_user_id == Order.mercado_livre_user_id,
        OrderLine.order_external_id == Order.external_id,
    )
    paid_filters = [
        Order.user_id == user.id,
        Order.status == "paid",
        Order.is_cancelled.is_(False),
        Order.created_at >= cutoff,
    ]
    if account_id is not None:
        paid_filters.append(Order.mercado_livre_user_id == account_id)

    units_stmt = (
        select(OrderLine.item_id, func.sum(OrderLine.quantity))
        .select_from(OrderLine)
        .join(Order, line_match)
        .where(*paid_filters)
        .group_by(OrderLine.item_id)
    )
    sales_by_item = {
        item_id: int(quantity)
        for item_id, quantity in db.execute(units_stmt).all()
    }

    paid_orders = db.scalar(select(func.count(Order.id)).where(*paid_filters)) or 0
    enriched_orders = db.scalar(
        select(func.count(func.distinct(Order.id)))
        .select_from(Order)
        .join(OrderLine, line_match)
        .where(*paid_filters)
    ) or 0

    # Select only worker checkpoint columns. Some deployments have not yet
    # applied the optional pending_listing_ids additive migration.
    finished_accounts = set(db.scalars(
        select(SyncJob.mercado_livre_user_id).where(
            SyncJob.user_id == user.id,
            SyncJob.status == "completed",
            SyncJob.phase == "done",
        )
    ).all())
    selected_accounts = {account_id} if account_id is not None else connected_accounts
    orders_complete = (
        bool(selected_accounts)
        and selected_accounts.issubset(finished_accounts)
        and paid_orders == enriched_orders
    )

    # The previous homepage queried only the first 1000 saved costs. Return all
    # tenant-owned costs; they are small metadata rows, not full product objects.
    costs = db.execute(select(
        Cost.external_id, Cost.unit_cost, Cost.manufacturer,
    ).where(Cost.user_id == user.id)).all()

    ads_stmt = select(
        Ads.external_id,
        func.sum(Ads.ad_spend),
        func.sum(Ads.attributed_revenue),
    ).where(
        Ads.user_id == user.id,
        Ads.report_date >= cutoff.date(),
    )
    if account_id is not None:
        ads_stmt = ads_stmt.where(Ads.mercado_livre_user_id == account_id)
    ads_rows = db.execute(ads_stmt.group_by(Ads.external_id)).all()

    return {
        "days": days,
        "account_id": account_id,
        "sales_by_item": sales_by_item,
        "paid_orders": int(paid_orders),
        "enriched_paid_orders": int(enriched_orders),
        "orders_complete": orders_complete,
        "costs": [
            {"external_id": item_id, "unit_cost": unit_cost,
             "manufacturer": manufacturer or ""}
            for item_id, unit_cost, manufacturer in costs
        ],
        "ads_by_item": {
            item_id: {
                "ad_spend": round(float(spend or 0), 2),
                "attributed_revenue": round(float(revenue or 0), 2),
            }
            for item_id, spend, revenue in ads_rows
        },
        "ads_source": "imported_reports_only",
    }

@router.post("/ads-daily/import")
def import_ads_reports(body: AdsImport, user: UserModel = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    # This endpoint imports already-exported Ads data. It does NOT claim API sync.
    authorized = {r[0] for r in db.execute(
        select(MercadoLivreConnectionModel.mercado_livre_user_id).where(
            MercadoLivreConnectionModel.user_id == user.id)).all()}
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
               offset: int = Query(default=0, ge=0),
               limit: int = Query(default=20, ge=1, le=20),
               phase: str = Query(default="legacy", pattern="^(legacy|listings|orders)$"),
               scroll_id: str | None = Query(default=None, min_length=1, max_length=4096),
               user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db),
               orders_created_from: datetime | None = None,
               orders_created_to: datetime | None = None):
    return await _sync_batch(
        account_id=account_id, offset=offset, limit=limit, phase=phase,
        scroll_id=scroll_id, user=user, db=db,
        orders_created_from=orders_created_from,
        orders_created_to=orders_created_to,
    )


async def _sync_batch(
    account_id: int | None, offset: int, limit: int, phase: str,
    scroll_id: str | None, user: UserModel, db: Session,
    orders_created_from: datetime | None = None,
    orders_created_to: datetime | None = None,
    prefetched_page: dict | None = None,
):
    """Persist one bounded batch. Scan+scroll retrieves listings beyond the 1000-offset cap.

    Each page is an independent transaction; repeating a page is safe because
    listings/products/orders are upserted by user, account and external ID.
    The client should continue using next_scroll_id until the API returns null,
    then paginate orders independently.
    """
    account = get_account(db, user.id, account_id)
    seller_id = account.mercado_livre_user_id
    source_key = f"ml:{seller_id}"
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.account_key == source_key))
    if source is None or not source.connected:
        raise HTTPException(409, "Fonte Mercado Livre não conectada.")
    if phase == "legacy" and offset > 1000:
        raise HTTPException(422, "Use phase=listings com paginação scan para mais de 1.000 anúncios.")
    if (orders_created_from is not None or orders_created_to is not None) and phase != "orders":
        raise HTTPException(422, "Período de pedidos só é válido para phase=orders.")
    if orders_created_from and orders_created_to and orders_created_from >= orders_created_to:
        raise HTTPException(422, "Período de pedidos inválido.")
    if scroll_id and phase != "listings":
        raise HTTPException(422, "scroll_id só é aceito para anúncios no modo scan.")

    page = {"results": []}
    next_scroll_id = None
    if phase != "orders":
        if prefetched_page is not None:
            # The durable worker has already stored this page's IDs and cursor.
            # A failed /items/bulk must retry the same IDs, not consume scan again.
            page = prefetched_page
        else:
            params = ({"search_type": "scan", **({"scroll_id": scroll_id} if scroll_id else {})}
                      if phase == "listings" else {"limit": limit, "offset": offset})
            try:
                page = await seller_request(db, account, f"/users/{seller_id}/items/search", params)
            except HTTPException as exc:
                if exc.status_code == 429:
                    raise HTTPException(429, exc.detail, headers={
                        **(exc.headers or {}), "X-FluxHub-Safe-Retry": "true"
                    }) from exc
                raise
        if not isinstance(page, dict):
            raise HTTPException(502, "Resposta de anúncios inválida.")
        if phase == "listings":
            cursor = page.get("scroll_id")
            if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096):
                raise HTTPException(502, "Cursor de anúncios inválido.")
            next_scroll_id = cursor

    ids = page.get("results", [])
    if not isinstance(ids, list):
        raise HTTPException(502, "Lista de anúncios inválida.")
    # Never silently skip malformed IDs or a partial bulk response; retry the page.
    if len(ids) > (100 if phase == "listings" else limit) or any(not isinstance(item, str) or not item.isalnum() for item in ids):
        raise HTTPException(502, "Lista de anúncios contém IDs inválidos.")
    # Persist every seller item. Pagination and rate limits are handled per batch.
    detail_ids = ids

    details = []
    unavailable_items = 0
    # /items/bulk returns a status PER ITEM. A permanently removed listing
    # (404/410) must not poison a complete page or block all seller orders.
    # Transient failures and incomplete responses still retry the staged IDs.
    for start in range(0, len(detail_ids), 20):
        chunk = detail_ids[start:start + 20]
        data = await seller_request(db, account, "/items/bulk", {"ids": ",".join(chunk)})
        if not isinstance(data, list):
            raise HTTPException(502, "Detalhes de anúncios inválidos.")
        accounted_for = set()
        for response_item in data:
            if not isinstance(response_item, dict):
                raise HTTPException(502, "Item retornado inválido.")
            body = response_item.get("body")
            item_id = str(response_item.get("id") or (
                body.get("id") if isinstance(body, dict) else "") or "")
            if item_id not in chunk or item_id in accounted_for:
                raise HTTPException(502, "Resposta de anúncio incompleta ou duplicada.")
            accounted_for.add(item_id)
            code = response_item.get("status_code", response_item.get("code"))
            if code == 200 and isinstance(body, dict):
                details.append(body)
            elif code in (404, 410):
                unavailable_items += 1
            elif code in (401, 403, 429):
                raise HTTPException(code, "Não foi possível consultar anúncios desta conta.")
            else:
                raise HTTPException(502, "Falha temporária em item do lote.")
        if accounted_for != set(chunk):
            raise HTTPException(502, "Lote de anúncios incompleto; página não avançada.")
    if unavailable_items:
        logger.warning(
            "ml_listing_details_unavailable seller=%s count=%s",
            seller_id, unavailable_items,
        )
    if not ids:
        next_scroll_id = None

    # Serialize upserts by seller to avoid duplicate records across concurrent jobs.
    db.scalar(select(CompanyDataSourceModel.id).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.account_key == source_key).with_for_update())
    listings_by_id = {row.external_id: row for row in db.scalars(
        select(Listing).where(
            Listing.user_id == user.id,
            Listing.mercado_livre_user_id == seller_id,
            Listing.external_id.in_(detail_ids))).all()}
    products_by_id = {row.external_id: row for row in db.scalars(
        select(ProductModel).where(
            ProductModel.company_id == user.id,
            ProductModel.source_key == source_key,
            ProductModel.external_id.in_(detail_ids))).all()}
    photos_by_id = {row.external_id: row for row in db.scalars(
        select(ListingPhoto).where(
            ListingPhoto.user_id == user.id,
            ListingPhoto.mercado_livre_user_id == seller_id,
            ListingPhoto.external_id.in_(detail_ids))).all()}
    imported = 0
    saved_products = 0
    for item in details:
        if int(item.get("seller_id") or 0) != seller_id:
            raise HTTPException(502, "Vendedor divergente nos detalhes do anúncio.")
        item_id = str(item.get("id") or "")
        if not item_id:
            raise HTTPException(502, "Anúncio sem identificador.")
        title = str(item.get("title") or "")[:255]
        photo = photos_by_id.get(item_id)
        if photo is None:
            photo = ListingPhoto(user_id=user.id, mercado_livre_user_id=seller_id,
                                 external_id=item_id)
            db.add(photo)
            photos_by_id[item_id] = photo
        photo.photo_url = thumbnail_from_item(item)
        listing = listings_by_id.get(item_id)
        if listing is None:
            listing = Listing(user_id=user.id, mercado_livre_user_id=seller_id,
                              external_id=item_id)
            db.add(listing)
            listings_by_id[item_id] = listing
        if listing is not None:
            listing.title = title
            listing.category_id = item.get("category_id")
            listing.status = str(item.get("status") or "unknown")[:40]
            listing.listing_type_id = item.get("listing_type_id")
            listing.price = float(item.get("price") or 0)
            listing.currency_id = item.get("currency_id")
            listing.published_at = parse_date(item["date_created"]) if item.get("date_created") else None
            listing.captured_at = now()
            imported += 1

        product = products_by_id.get(item_id)
        if product is None:
            product = ProductModel(company_id=user.id, source_key=source_key,
                                   external_id=item_id, category="", brand="")
            db.add(product)
            products_by_id[item_id] = product
        if product is not None:
            product.title = title
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
            saved_products += 1

    # Orders use a separate offset. A listing scan must not stop order pagination.
    orders_unavailable = False
    saved_orders = 0
    next_orders_offset = None
    orders_page_total = None
    if phase != "listings":
        try:
            order_params = {"seller": seller_id, "limit": limit, "offset": offset,
                            "sort": "date_desc"}
            if orders_created_from:
                order_params["order.date_created.from"] = orders_created_from.isoformat()
            if orders_created_to:
                order_params["order.date_created.to"] = orders_created_to.isoformat()
            orders_page = await seller_request(db, account, "/orders/search", order_params)
            if not isinstance(orders_page, dict) or not isinstance(orders_page.get("results"), list):
                raise HTTPException(502, "Resposta de pedidos inválida.")
        except HTTPException as error:
            if error.status_code in (401, 403, 429):
                # Auth failures must be visible, never mislabeled as transient 502.
                # 429 keeps the same order offset for safe retry.
                raise
            orders_page = {"results": []}
            orders_unavailable = True
        if not orders_unavailable:
            order_results = orders_page["results"]
            if len(order_results) > limit:
                raise HTTPException(502, "Página de pedidos maior do que o solicitado.")
            paging = orders_page.get("paging") or {}
            total = paging.get("total") if isinstance(paging, dict) else None
            orders_page_total = total if isinstance(total, int) else None
            if len(order_results) == limit and (not isinstance(total, int) or offset + len(order_results) < total):
                next_orders_offset = offset + len(order_results)
        order_ids = [str(item["id"]) for item in orders_page.get("results", [])
                     if isinstance(item, dict) and item.get("id")]
        orders_by_id = {row.external_id: row for row in db.scalars(
            select(Order).where(
                Order.user_id == user.id,
                Order.mercado_livre_user_id == seller_id,
                Order.external_id.in_(order_ids))).all()}
        existing_lines = db.scalars(select(OrderLine).where(
            OrderLine.user_id == user.id,
            OrderLine.mercado_livre_user_id == seller_id,
            OrderLine.order_external_id.in_(order_ids))).all()
        lines_by_key = {(line.order_external_id, line.line_number): line
                        for line in existing_lines}
        buyers_by_order = {row.order_external_id: row for row in db.scalars(
            select(BuyerOrder).where(
                BuyerOrder.user_id == user.id,
                BuyerOrder.mercado_livre_user_id == seller_id,
                BuyerOrder.order_external_id.in_(order_ids))).all()}
        saved_orders = 0
        for item in orders_page.get("results", []):
            if not isinstance(item, dict) or not item.get("id"):
                raise HTTPException(502, "Pedido sem identificador.")
            seller = item.get("seller") or {}
            if int(seller.get("id") or 0) != seller_id:
                raise HTTPException(502, "Vendedor divergente nos pedidos.")
            external_id = str(item["id"])
            row = orders_by_id.get(external_id)
            if row is None:
                row = Order(user_id=user.id, mercado_livre_user_id=seller_id,
                            external_id=external_id)
                db.add(row)
                orders_by_id[external_id] = row
            row.status = str(item.get("status") or "")[:40]
            row.total_amount = float(item.get("total_amount") or 0)
            row.created_at = parse_date(item.get("date_created"))
            row.is_cancelled = row.status == "cancelled"
            buyer = item.get("buyer") or {}
            raw_buyer_id = buyer.get("id") if isinstance(buyer, dict) else None
            if raw_buyer_id is not None and str(raw_buyer_id).isdigit():
                buyer_row = buyers_by_order.get(external_id)
                if buyer_row is None:
                    buyer_row = BuyerOrder(
                        user_id=user.id, mercado_livre_user_id=seller_id,
                        order_external_id=external_id)
                    db.add(buyer_row)
                    buyers_by_order[external_id] = buyer_row
                buyer_row.buyer_id = int(raw_buyer_id)
            line_items = item.get("order_items") or []
            if not isinstance(line_items, list):
                raise HTTPException(502, "Itens do pedido inválidos.")
            for position, source_line in enumerate(line_items):
                if not isinstance(source_line, dict):
                    raise HTTPException(502, "Item de pedido inválido.")
                item_data = source_line.get("item") or {}
                if not isinstance(item_data, dict):
                    raise HTTPException(502, "Identificação do produto inválida.")
                item_external_id = str(item_data.get("id") or "")[:100]
                if not item_external_id:
                    raise HTTPException(502, "Produto de pedido sem identificador.")
                key = (external_id, position)
                line = lines_by_key.get(key)
                if line is None:
                    line = OrderLine(user_id=user.id,
                                     mercado_livre_user_id=seller_id,
                                     order_external_id=external_id,
                                     line_number=position)
                    db.add(line)
                    lines_by_key[key] = line
                line.item_id = item_external_id
                line.title = str(item_data.get("title") or "Produto sem título")[:255]
                line.sku = str(item_data.get("seller_custom_field") or "")[:150] or None
                line.quantity = int(source_line.get("quantity") or 0)
                line.unit_price = float(source_line.get("unit_price") or 0)
                line.sale_fee = optional_money(source_line.get("sale_fee"))
            for key, previous in list(lines_by_key.items()):
                if key[0] == external_id and key[1] >= len(line_items):
                    db.delete(previous)
                    del lines_by_key[key]
            saved_orders += 1

    # The visit metric is optional and is fetched once on the initial listings batch.
    visits_synced = 0
    visits_error = False
    if phase == "legacy" or (phase == "listings" and not scroll_id):
        visits_synced = 0
        visits_error = False
        try:
            visitor_data = await seller_request(
                db, account,
                f"/users/{seller_id}/items_visits/time_window",
                {"last": 30, "unit": "day"})
            for entry in visitor_data.get("results", []):
                if not isinstance(entry, dict) or entry.get("total") is None:
                    continue
                day = date.fromisoformat(str(entry.get("date", ""))[:10])
                count = int(entry["total"])
                if count < 0:
                    continue
                record = db.scalar(select(Visits).where(
                    Visits.user_id == user.id,
                    Visits.mercado_livre_user_id == seller_id,
                    Visits.report_date == day))
                if record is None:
                    record = Visits(user_id=user.id, mercado_livre_user_id=seller_id,
                                    report_date=day)
                    db.add(record)
                record.total_visits = count
                record.captured_at = now()
                visits_synced += 1
        except (HTTPException, TypeError, ValueError, OverflowError):
            visits_error = True

    source.last_synced_at = now()
    source.status_message = (f"{len(ids)} anúncios consultados; {imported} armazenados/atualizados e {saved_orders} pedidos nesta página."
                             + (" Consulta de pedidos indisponível." if orders_unavailable else ""))
    db.commit()
    return {
        "source": source_key, "listings": len(ids), "saved_listings": imported,
        "listings_total": (page.get("paging") or {}).get("total") if isinstance(page.get("paging"), dict) else None,
        "saved_products": saved_products, "storage_limit": None,
        "unavailable_listings": unavailable_items,
        "orders": saved_orders, "orders_page_total": orders_page_total,
        "phase": phase, "offset": offset, "limit": limit,
        "next_scroll_id": next_scroll_id if phase == "listings" else None,
        "next_orders_offset": next_orders_offset,
        "listings_complete": phase == "orders" or (phase == "listings" and next_scroll_id is None),
        "orders_complete": phase != "listings" and not orders_unavailable and next_orders_offset is None,
        "next_offset": offset + limit if phase == "legacy" and len(ids) == limit and offset + limit <= 1000 else None,
        "partial": next_scroll_id is not None or next_orders_offset is not None or orders_unavailable,
        "ads_data_synced": False, "visit_days_synced": visits_synced,
        "visits_unavailable": visits_error, "orders_unavailable": orders_unavailable,
    }

@router.get("/ads-performance")
def ads_performance(
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if account_id is not None:
        get_account(db, user.id, account_id)
    filters = [Listing.user_id == user.id]
    if account_id is not None:
        filters.append(Listing.mercado_livre_user_id == account_id)
    # Database aggregation stays bounded as a seller grows beyond 10k items.
    counts = db.execute(select(Listing.status, func.count(Listing.id))
                        .where(*filters).group_by(Listing.status)).all()
    statuses = {status or "unknown": count for status, count in counts}
    total = sum(statuses.values())
    avg = db.scalar(select(func.avg(Listing.price)).where(*filters))
    return {"total_listings": total, "by_status": statuses,
            "active_listings": statuses.get("active", 0),
            "average_price": round(float(avg), 2) if avg is not None else None,
            "visits_30d": None, "visits_data_available": False,
            "low_relevance_listings": None,
            "note": "Visitas e vendas atribuídas não são inferidas sem relatório real."}


@router.get("/order-status")
def order_status_summary(
    days: int = Query(default=30, ge=1, le=365),
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Counts across every persisted order, not just the first UI page."""
    if account_id is not None:
        get_account(db, user.id, account_id)
    filters = [Order.user_id == user.id,
               Order.created_at >= now() - timedelta(days=days)]
    if account_id is not None:
        filters.append(Order.mercado_livre_user_id == account_id)
    rows = db.execute(select(Order.status, func.count(Order.id))
                      .where(*filters).group_by(Order.status)).all()
    return {"days": days, "account_id": account_id,
            "total_orders": sum(count for _, count in rows),
            "by_status": {status or "unknown": count for status, count in rows}}


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


@router.get("/timeline")
def timeline(days: int = Query(default=30, ge=1, le=90),
             account_id: int | None = None,
             user: UserModel = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Actual daily order snapshots and ML visits, no invented zero-filled days."""
    seller = account_id
    if seller is not None:
        get_account(db, user.id, seller)
    threshold = date.today() - timedelta(days=days)
    order_query = select(Order).where(Order.user_id == user.id,
                                      Order.created_at >= now() - timedelta(days=days))
    visit_query = select(Visits).where(Visits.user_id == user.id,
                                       Visits.report_date >= threshold)
    if seller is not None:
        order_query = order_query.where(Order.mercado_livre_user_id == seller)
        visit_query = visit_query.where(Visits.mercado_livre_user_id == seller)
    orders = db.scalars(order_query).all()
    visits = db.scalars(visit_query).all()
    daily = defaultdict(lambda: {"orders": 0, "cancelled_orders": 0,
                                 "gross_non_cancelled": 0.0, "visits": None})
    for order in orders:
        day = order.created_at.date().isoformat()
        daily[day]["orders"] += 1
        if order.is_cancelled:
            daily[day]["cancelled_orders"] += 1
        else:
            daily[day]["gross_non_cancelled"] += order.total_amount
    for visit in visits:
        day = visit.report_date.isoformat()
        daily[day]["visits"] = (daily[day]["visits"] or 0) + visit.total_visits
    return {"days": days, "partial_data": True,
            "seller_id": seller,
            "data": [
                {"date": day, **{key: round(value, 2)
                 if key == "gross_non_cancelled" else value
                 for key, value in measures.items()}}
                for day, measures in sorted(daily.items())
            ],
            "note": "Histórico limitado às páginas de pedidos sincronizadas; visitas reais quando disponíveis."}
