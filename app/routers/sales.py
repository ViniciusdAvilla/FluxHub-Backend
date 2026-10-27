"""Fast, tenant-scoped paid-order revenue calculated inside PostgreSQL.

The endpoint intentionally selects only stable columns of sync_jobs so that
an unapplied additive worker migration never prevents the revenue dashboard
from reading already-saved orders.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    CompanyDataSourceModel,
    MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreSyncJobModel as Job,
    UserModel,
)
from app.database.session import get_db

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.get("/")
def sales(
    days: int = Query(default=30, ge=1, le=365),
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    not_cancelled = Order.is_cancelled.is_(False)
    paid = not_cancelled & (Order.status == "paid")
    stmt = select(
        func.count(Order.id).label("total_orders"),
        func.coalesce(func.sum(case((not_cancelled, 1), else_=0)), 0).label("non_cancelled_orders"),
        func.coalesce(func.sum(case((paid, 1), else_=0)), 0).label("paid_order_count"),
        func.coalesce(func.sum(case((not_cancelled, Order.total_amount), else_=0.0)), 0.0)
            .label("gross_non_cancelled"),
        func.coalesce(func.sum(case((paid, Order.total_amount), else_=0.0)), 0.0)
            .label("gross_paid"),
    ).where(
        Order.user_id == user.id,
        Order.created_at >= cutoff,
    )
    if account_id is not None:
        stmt = stmt.where(Order.mercado_livre_user_id == account_id)

    # One aggregate row instead of hydrating every seller order into Python.
    amounts = db.execute(stmt).one()

    source_keys = select(CompanyDataSourceModel.account_key).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.enabled.is_(True),
        CompanyDataSourceModel.connected.is_(True),
    )
    if account_id is not None:
        source_keys = source_keys.where(
            CompanyDataSourceModel.account_key == f"ml:{account_id}"
        )
    enabled_accounts = db.scalars(source_keys).all()
    # Avoid SELECT Job.*: old installations may not yet have the
    # pending_listing_ids columns required only by the sync worker.
    finished_ids = set(db.scalars(select(Job.mercado_livre_user_id).where(
        Job.user_id == user.id,
        Job.status == "completed",
        Job.phase == "done",
    )).all()) if enabled_accounts else set()
    complete = bool(enabled_accounts) and all(
        key.startswith("ml:") and key[3:].isdecimal()
        and int(key[3:]) in finished_ids
        for key in enabled_accounts
    )
    paid_count = int(amounts.paid_order_count)
    return {
        "days": days,
        "account_id": account_id,
        "total_orders": int(amounts.total_orders),
        "non_cancelled_orders": int(amounts.non_cancelled_orders),
        "paid_order_count": paid_count,
        "gross_paid_order_amount": round(float(amounts.gross_paid), 2)
            if paid_count or complete else None,
        "gross_non_cancelled_order_amount": round(float(amounts.gross_non_cancelled), 2),
        "data_complete": complete,
        "partial_data": not complete,
        "note": (
            "Valor bruto dos pedidos pagos registrados. Não corresponde ao "
            "valor liquidado no Mercado Pago; taxas, frete e devoluções podem "
            "alterar o valor recebido. Histórico parcial enquanto a "
            "sincronização de pedidos não terminar."
        ),
    }
