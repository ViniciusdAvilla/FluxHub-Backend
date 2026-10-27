"""Persisted seller orders, product photos and opt-in buyer detail lookups.

PII is NEVER stored in PostgreSQL or returned with ordinary list endpoints.
Only authenticated tenant owners may request live buyer details for an order
which already belongs to their authorized Mercado Livre account.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    UserModel, MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreOrderLineModel as OrderLine,
    MercadoLivreListingPhotoModel as ListingPhoto,
    MercadoLivreProductCostModel as Cost,
    MercadoLivreBuyerOrderModel as BuyerOrder,
    MercadoLivreBuyerAccessAuditModel as BuyerAccessAudit,
)
from app.database.session import get_db
from app.routers.mercado_livre import get_account, seller_request

router = APIRouter(prefix="/orders", tags=["Orders"])


def _base(user_id: int, account_id: int | None, days: int | None):
    query = select(Order).where(Order.user_id == user_id)
    if account_id is not None:
        query = query.where(Order.mercado_livre_user_id == account_id)
    if days is not None:
        query = query.where(
            Order.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    return query


def _matching(query, search: str | None):
    if not search:
        return query
    pattern = f"%{search.strip()}%"
    item_exists = exists(select(OrderLine.id).where(
        OrderLine.user_id == Order.user_id,
        OrderLine.mercado_livre_user_id == Order.mercado_livre_user_id,
        OrderLine.order_external_id == Order.external_id,
        or_(OrderLine.title.ilike(pattern), OrderLine.sku.ilike(pattern),
            OrderLine.item_id.ilike(pattern))))
    return query.where(or_(Order.external_id.ilike(pattern), item_exists))


def _decorate(db: Session, rows: list[Order]):
    if not rows:
        return []
    by_seller: dict[int, list[str]] = {}
    for order in rows:
        if order.mercado_livre_user_id is not None:
            by_seller.setdefault(order.mercado_livre_user_id, []).append(order.external_id)
    lines: dict[tuple[int, str], list[OrderLine]] = {}
    photos: dict[tuple[int, str], str | None] = {}
    buyers: dict[tuple[int, str], int] = {}
    for seller_id, ids in by_seller.items():
        owner_id = rows[0].user_id
        for line in db.scalars(select(OrderLine).where(
            OrderLine.user_id == owner_id,
            OrderLine.mercado_livre_user_id == seller_id,
            OrderLine.order_external_id.in_(ids)).order_by(
                OrderLine.order_external_id, OrderLine.line_number)).all():
            lines.setdefault((seller_id, line.order_external_id), []).append(line)
        for row in db.scalars(select(BuyerOrder).where(
            BuyerOrder.user_id == owner_id,
            BuyerOrder.mercado_livre_user_id == seller_id,
            BuyerOrder.order_external_id.in_(ids))).all():
            buyers[(seller_id, row.order_external_id)] = row.buyer_id
        ids_for_photos = list({line.item_id
                               for order_id in ids
                               for line in lines.get((seller_id, order_id), [])})
        if ids_for_photos:
            for photo in db.scalars(select(ListingPhoto).where(
                ListingPhoto.user_id == owner_id,
                ListingPhoto.mercado_livre_user_id == seller_id,
                ListingPhoto.external_id.in_(ids_for_photos))).all():
                photos[(seller_id, photo.external_id)] = photo.photo_url

    all_item_ids = list({line.item_id for group in lines.values() for line in group})
    costs = {row.external_id: row for row in db.scalars(select(Cost).where(
        Cost.user_id == rows[0].user_id, Cost.external_id.in_(all_item_ids))).all()} if all_item_ids else {}

    result = []
    for row in rows:
        seller_id = row.mercado_livre_user_id
        item_list = []
        for line in lines.get((seller_id, row.external_id), []):
            cost = costs.get(line.item_id)
            item_list.append({
                "item_id": line.item_id, "title": line.title, "sku": line.sku,
                "quantity": line.quantity, "unit_price": line.unit_price,
                "sale_fee": line.sale_fee,
                "thumbnail": photos.get((seller_id, line.item_id)),
                "unit_cost": cost.unit_cost if cost else None,
                "tax_percent": cost.tax_percent if cost else None,
                "configured_shipping": cost.shipping_cost if cost else None,
                "other_cost": cost.other_cost if cost else None,
            })
        result.append({
            "external_id": row.external_id,
            "seller_id": seller_id,
            "status": row.status,
            "total_amount": row.total_amount,
            "created_at": row.created_at,
            "is_cancelled": row.is_cancelled,
            "buyer_id": buyers.get((seller_id, row.external_id)),
            "items": item_list,
        })
    return result


@router.get("/")
def list_orders(
    account_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    days: int | None = Query(default=None, ge=1, le=365),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(_base(user.id, account_id, days).order_by(
        Order.created_at.desc(), Order.id.desc()).offset(offset).limit(limit)).all()
    return _decorate(db, rows)


@router.get("/paged")
def paged_orders(
    account_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    days: int | None = Query(default=None, ge=1, le=365),
    status: str | None = Query(default=None, max_length=40),
    search: str | None = Query(default=None, max_length=100),
    min_amount: float | None = Query(default=None, ge=0),
    max_amount: float | None = Query(default=None, ge=0),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(422, "Faixa de valor inválida.")
    source = _matching(_base(user.id, account_id, days), search)
    summary = select(Order.status, func.count(Order.id)).where(
        Order.user_id == user.id)
    if account_id is not None:
        summary = summary.where(Order.mercado_livre_user_id == account_id)
    if days is not None:
        summary = summary.where(
            Order.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    # Status counters follow the current period and search, independent of status tab.
    if search:
        summary_ids = _matching(_base(user.id, account_id, days), search).with_only_columns(
            Order.id)
        summary = summary.where(Order.id.in_(summary_ids))
    if min_amount is not None:
        source = source.where(Order.total_amount >= min_amount)
        summary = summary.where(Order.total_amount >= min_amount)
    if max_amount is not None:
        source = source.where(Order.total_amount <= max_amount)
        summary = summary.where(Order.total_amount <= max_amount)
    # Re-read status counts after applying the amount range.
    counts = {key: amount for key, amount in db.execute(
        summary.group_by(Order.status)).all()}
    if status:
        source = source.where(Order.status == status)
    total = db.scalar(source.with_only_columns(
        func.count(Order.id)).order_by(None)) or 0
    rows = db.scalars(source.order_by(
        Order.created_at.desc(), Order.id.desc()).offset(offset).limit(limit)).all()
    paid_query = _matching(_base(user.id, account_id, days), search).where(
        Order.status == "paid")
    if min_amount is not None:
        paid_query = paid_query.where(Order.total_amount >= min_amount)
    if max_amount is not None:
        paid_query = paid_query.where(Order.total_amount <= max_amount)
    paid_sum = db.scalar(paid_query.with_only_columns(
        func.sum(Order.total_amount)).order_by(None)) or 0
    return {
        "orders": _decorate(db, rows), "total": total,
        "status_counts": counts, "limit": limit, "offset": offset,
        "gross_paid_amount": round(float(paid_sum), 2),
        "is_complete": len(rows) < limit or offset + len(rows) >= total,
    }


@router.get("/buyers")
def buyer_summary(
    account_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = [BuyerOrder.user_id == user.id]
    if account_id is not None:
        filters.append(BuyerOrder.mercado_livre_user_id == account_id)
    buyers = db.execute(select(
        BuyerOrder.mercado_livre_user_id, BuyerOrder.buyer_id,
        func.count(Order.id), func.sum(Order.total_amount),
    ).join(Order, and_(
        Order.user_id == BuyerOrder.user_id,
        Order.mercado_livre_user_id == BuyerOrder.mercado_livre_user_id,
        Order.external_id == BuyerOrder.order_external_id,
    )).where(*filters).group_by(
        BuyerOrder.mercado_livre_user_id, BuyerOrder.buyer_id
    ).order_by(func.count(Order.id).desc()).offset(offset).limit(limit)).all()
    return [{"account_id": seller, "buyer_id": buyer,
             "orders_count": count, "gross_order_total": float(total or 0)}
            for seller, buyer, count, total in buyers]



@router.get("/buyers/{buyer_id}")
def buyer_orders(
    buyer_id: int,
    account_id: int = Query(ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # The account constraint is compulsory: buyer ID may appear in many shops.
    owned_orders = select(BuyerOrder.order_external_id).where(
        BuyerOrder.user_id == user.id,
        BuyerOrder.mercado_livre_user_id == account_id,
        BuyerOrder.buyer_id == buyer_id)
    rows = db.scalars(select(Order).where(
        Order.user_id == user.id,
        Order.mercado_livre_user_id == account_id,
        Order.external_id.in_(owned_orders)).order_by(
            Order.created_at.desc()).offset(offset).limit(limit)).all()
    return _decorate(db, rows)


def _order_for_user(db: Session, user_id: int, order_id: str,
                    account_id: int | None = None):
    if not order_id.isdigit() or len(order_id) > 30:
        raise HTTPException(422, "Identificador de pedido inválido.")
    query = select(Order).where(Order.user_id == user_id,
                                Order.external_id == order_id)
    if account_id is not None:
        query = query.where(Order.mercado_livre_user_id == account_id)
    rows = db.scalars(query.limit(2)).all()
    if not rows:
        raise HTTPException(404, "Pedido não encontrado nesta conta.")
    if len(rows) > 1:
        raise HTTPException(409, "Informe account_id para este pedido.")
    return rows[0]


@router.get("/{order_id}")
def order_detail(
    order_id: str,
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _decorate(db, [_order_for_user(db, user.id, order_id, account_id)])[0]


def _name(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return str(raw.get("name") or "").strip() or None
    return str(raw).strip() if isinstance(raw, str) else None


def _address(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    # Explicit allow-list prevents accidental leak of the complete upstream payload.
    return {
        "receiver_name": raw.get("receiver_name"),
        "receiver_phone": raw.get("receiver_phone"),
        "contact": raw.get("contact"),
        "street": raw.get("street_name"),
        "number": raw.get("street_number"),
        "address_line": raw.get("address_line"),
        "zip_code": raw.get("zip_code"),
        "neighborhood": _name(raw.get("neighborhood")),
        "city": _name(raw.get("city")),
        "state": _name(raw.get("state")),
        "country": _name(raw.get("country")),
    }


@router.get("/{order_id}/buyer")
async def buyer_detail(
    order_id: str,
    response: Response,
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Explicit access only. Never cache PII or persist it in order snapshots."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    order = _order_for_user(db, user.id, order_id, account_id)
    seller_id = order.mercado_livre_user_id
    if seller_id is None:
        raise HTTPException(409, "Pedido sem conta Mercado Livre identificada.")
    account = get_account(db, user.id, seller_id)
    raw = await seller_request(db, account, f"/orders/{order_id}")
    if not isinstance(raw, dict) or str(raw.get("id")) != order_id or (
        str((raw.get("seller") or {}).get("id")) != str(seller_id)
    ):
        raise HTTPException(404, "Pedido indisponível na conta autorizada.")
    raw_buyer = raw.get("buyer") or {}
    if not isinstance(raw_buyer, dict):
        raw_buyer = {}
    buyer = {
        "id": raw_buyer.get("id"),
        "nickname": raw_buyer.get("nickname"),
        "first_name": raw_buyer.get("first_name"),
        "last_name": raw_buyer.get("last_name"),
        "email": raw_buyer.get("email"),
        "phone": raw_buyer.get("phone"),
    }
    shipping_address = None
    shipping_available = False
    shipping_id = (raw.get("shipping") or {}).get("id")
    if shipping_id is not None and str(shipping_id).isdigit():
        try:
            shipped = await seller_request(
                db, account, f"/shipments/{shipping_id}",
                {"views": "destination"}, headers={"X-Api-Version": "2"})
            if isinstance(shipped, dict):
                shipping_address = _address(
                    shipped.get("receiver_address") or shipped.get("receiver_address_info"))
                shipping_available = shipping_address is not None
        except HTTPException:
            # Platform may deny PII depending on seller scope or shipment type.
            pass

    fiscal = None
    raw_billing = raw_buyer.get("billing_info") or {}
    billing_id = raw_billing.get("id") if isinstance(raw_billing, dict) else None
    raw_items = raw.get("order_items") or []
    first_item = raw_items[0] if isinstance(raw_items, list) and raw_items else {}
    item_id = ((first_item.get("item") or {}).get("id") or "") if isinstance(first_item, dict) else ""
    site_id = item_id[:3] if isinstance(item_id, str) else ""
    if billing_id is not None and str(billing_id).isdigit() and (
        len(site_id) == 3 and site_id.isalpha() and site_id.isupper()
    ):
        try:
            bill = await seller_request(
                db, account, f"/orders/billing-info/{site_id}/{billing_id}")
            if isinstance(bill, dict):
                b = bill.get("buyer") or bill.get("billing_info") or bill
                if isinstance(b, dict):
                    identity = b.get("identification") or {}
                    identity = identity if isinstance(identity, dict) else {}
                    fiscal = {
                        "name": b.get("name") or b.get("full_name"),
                        "document_type": identity.get("type") or b.get("doc_type"),
                        "document_number": identity.get("number") or b.get("doc_number"),
                    }
        except HTTPException:
            pass

    db.add(BuyerAccessAudit(user_id=user.id, mercado_livre_user_id=seller_id,
                            order_external_id=order_id))
    db.commit()
    return {
        "order_id": order_id, "account_id": seller_id,
        "buyer": buyer, "delivery_address": shipping_address,
        "delivery_data_available": shipping_available,
        "fiscal": fiscal, "fiscal_data_available": fiscal is not None,
        "source": "mercado_livre_authorized_live",
        "stored_personal_data": False,
    }
