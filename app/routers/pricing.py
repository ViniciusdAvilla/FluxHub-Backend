"""Authenticated per-listing cost/fee quote for the FluxHub profit calculator."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import (
    UserModel, MercadoLivreProductCostModel as Cost,
    MercadoLivreListingPhotoModel as ListingPhoto,
)
from app.database.session import get_db
from app.routers.mercado_livre import get_account, seller_request
from app.routers.analytics import thumbnail_from_item

router = APIRouter(prefix="/pricing", tags=["Pricing"])


@router.get("/{item_id}")
async def listing_quote(
    item_id: str,
    account_id: int = Query(ge=1),
    price: float | None = Query(default=None, gt=0, le=1_000_000_000),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not item_id.isalnum() or len(item_id) > 100:
        raise HTTPException(422, "Anúncio inválido.")
    account = get_account(db, user.id, account_id)
    detail = await seller_request(db, account, f"/items/{item_id}")
    if not isinstance(detail, dict) or (
        int(detail.get("seller_id") or 0) != account_id
    ):
        raise HTTPException(404, "Anúncio não pertence ao vendedor conectado.")
    cost = db.scalar(select(Cost).where(
        Cost.user_id == user.id, Cost.external_id == item_id))
    photo = db.scalar(select(ListingPhoto).where(
        ListingPhoto.user_id == user.id,
        ListingPhoto.mercado_livre_user_id == account_id,
        ListingPhoto.external_id == item_id))
    sell_price = price or float(detail.get("price") or 0)
    category = detail.get("category_id")
    listing_type = detail.get("listing_type_id")
    currency = detail.get("currency_id") or "BRL"
    shipping = detail.get("shipping") or {}
    shipping = shipping if isinstance(shipping, dict) else {}
    quoted_fee = None
    fee_breakdown = None
    quote_unavailable = False
    if sell_price > 0 and isinstance(category, str) and (
        isinstance(listing_type, str)
    ):
        params = {
            "category_id": category, "price": sell_price,
            "listing_type_id": listing_type, "currency_id": currency,
        }
        if shipping.get("mode"):
            params["shipping_mode"] = shipping["mode"]
        if shipping.get("logistic_type"):
            params["logistic_type"] = shipping["logistic_type"]
        try:
            result = await seller_request(db, account, "/sites/MLB/listing_prices", params)
            if isinstance(result, list):
                match = next((part for part in result if isinstance(part, dict)
                              and part.get("listing_type_id") == listing_type), None)
                result = match or (result[0] if result else {})
            if isinstance(result, dict):
                amount = result.get("sale_fee_amount")
                quoted_fee = float(amount) if amount is not None else None
                fee_breakdown = result.get("sale_fee_details") if isinstance(
                    result.get("sale_fee_details"), dict) else None
        except (HTTPException, TypeError, ValueError):
            quote_unavailable = True

    return {
        "item_id": item_id,
        "account_id": account_id,
        "title": str(detail.get("title") or "")[:255],
        "thumbnail": thumbnail_from_item(detail) or (
            photo.photo_url if photo else None),
        "price": sell_price,
        "category_id": category,
        "listing_type_id": listing_type,
        "currency_id": currency,
        "fee_quote": quoted_fee,
        "fee_details": fee_breakdown,
        "fee_estimate_unavailable": quote_unavailable,
        "quoted_for_price": sell_price,
        "cost": {
            "unit_cost": cost.unit_cost,
            "tax_percent": cost.tax_percent,
            "shipping_cost": cost.shipping_cost,
            "other_cost": cost.other_cost,
            "marketplace_fee_percent": cost.marketplace_fee_percent,
        } if cost else None,
        "cost_source": "fluxhub_registered" if cost else "not_registered",
        "fee_source": "mercado_livre_quote" if quoted_fee is not None else "unavailable",
    }


class CostUpdate(BaseModel):
    unit_cost: float = Field(ge=0, le=1_000_000_000)
    tax_percent: float = Field(ge=0, le=100)
    shipping_cost: float = Field(ge=0, le=1_000_000_000)
    other_cost: float = Field(ge=0, le=1_000_000_000)
    marketplace_fee_percent: float | None = Field(default=None, ge=0, le=100)


@router.patch("/{item_id}/cost")
def save_item_cost(
    item_id: str,
    body: CostUpdate,
    account_id: int = Query(ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not item_id.isalnum() or len(item_id) > 100:
        raise HTTPException(422, "Anúncio inválido.")
    # Ensure this is an already imported item from this tenant + account.
    owner = db.scalar(select(Listing.id).where(
        Listing.user_id == user.id,
        Listing.mercado_livre_user_id == account_id,
        Listing.external_id == item_id))
    if owner is None:
        raise HTTPException(404, "Anúncio não encontrado na sua conta.")
    cost = db.scalar(select(Cost).where(
        Cost.user_id == user.id, Cost.external_id == item_id))
    if cost is None:
        cost = Cost(user_id=user.id, external_id=item_id,
                    manufacturer="")
        db.add(cost)
    # Preserve manufacturer: it must never be overwritten by the calculator.
    cost.unit_cost = body.unit_cost
    cost.tax_percent = body.tax_percent
    cost.shipping_cost = body.shipping_cost
    cost.other_cost = body.other_cost
    cost.marketplace_fee_percent = body.marketplace_fee_percent
    db.commit()
    return {"updated": True, "item_id": item_id}
