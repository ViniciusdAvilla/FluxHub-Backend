"""Tenant-scoped, persisted Mercado Livre orders (no fabricated records)."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import UserModel, MercadoLivreOrderSnapshotModel
from app.database.session import get_db

router = APIRouter(prefix="/orders", tags=["Orders"])


def show(item):
    return {"external_id": item.external_id, "seller_id": item.mercado_livre_user_id,
            "status": item.status, "total_amount": item.total_amount,
            "created_at": item.created_at, "is_cancelled": item.is_cancelled}


@router.get("/")
def list_orders(account_id: int | None = None,
                limit: int = Query(default=50, ge=1, le=100),
                offset: int = Query(default=0, ge=0),
                days: int | None = Query(default=None, ge=1, le=365),
                user: UserModel = Depends(get_current_user),
                db: Session = Depends(get_db)):
    query = select(MercadoLivreOrderSnapshotModel).where(
        MercadoLivreOrderSnapshotModel.user_id == user.id)
    if days is not None:
        query = query.where(MercadoLivreOrderSnapshotModel.created_at >=
                            datetime.now(timezone.utc) - timedelta(days=days))
    if account_id is not None:
        query = query.where(
            MercadoLivreOrderSnapshotModel.mercado_livre_user_id == account_id)
    rows = db.scalars(query.order_by(
        MercadoLivreOrderSnapshotModel.created_at.desc()).offset(offset).limit(limit)).all()
    return [show(row) for row in rows]


@router.get("/{order_id}")
def order_detail(order_id: str,
                 user: UserModel = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    row = db.scalar(select(MercadoLivreOrderSnapshotModel).where(
        MercadoLivreOrderSnapshotModel.user_id == user.id,
        MercadoLivreOrderSnapshotModel.external_id == order_id))
    if row is None:
        raise HTTPException(404, "Pedido não encontrado para este usuário.")
    return show(row)
