"""Order-derived revenue indicators; never confuse listing prices with sales.

These amounts come from persisted seller orders, not from the Mercado Pago
settlement ledger. Partial import coverage is explicitly flagged.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    CompanyDataSourceModel, MercadoLivreOrderSnapshotModel, MercadoLivreSyncJobModel,
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
    query = select(MercadoLivreOrderSnapshotModel).where(
        MercadoLivreOrderSnapshotModel.user_id == user.id,
        MercadoLivreOrderSnapshotModel.created_at >= cutoff,
    )
    if account_id is not None:
        query = query.where(
            MercadoLivreOrderSnapshotModel.mercado_livre_user_id == account_id
        )
    rows = db.scalars(query).all()
    non_cancelled = [row for row in rows if not row.is_cancelled]
    paid = [row for row in non_cancelled if row.status == "paid"]

    sources_query = select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.enabled.is_(True),
        CompanyDataSourceModel.connected.is_(True),
    )
    if account_id is not None:
        sources_query = sources_query.where(
            CompanyDataSourceModel.account_key == f"ml:{account_id}"
        )
    sources = db.scalars(sources_query).all()
    jobs = db.scalars(select(MercadoLivreSyncJobModel).where(
        MercadoLivreSyncJobModel.user_id == user.id,
    )).all()
    completed_accounts = {
        job.mercado_livre_user_id for job in jobs
        if job.status == "completed" and job.phase == "done"
    }
    coverage_complete = bool(sources) and all(
        source.account_key.removeprefix("ml:").isdecimal()
        and int(source.account_key.removeprefix("ml:")) in completed_accounts
        for source in sources
    )
    gross_paid = round(sum(row.total_amount for row in paid), 2)
    return {
        "days": days,
        "account_id": account_id,
        "total_orders": len(rows),
        "non_cancelled_orders": len(non_cancelled),
        "paid_order_count": len(paid),
        "gross_paid_order_amount": (
            gross_paid if paid or coverage_complete else None
        ),
        "gross_non_cancelled_order_amount": round(sum(
            row.total_amount for row in non_cancelled), 2),
        "data_complete": coverage_complete,
        "partial_data": not coverage_complete,
        "note": (
            "Valor bruto dos pedidos pagos cadastrados. Não é dinheiro "
            "liquidado no Mercado Pago; taxas, frete e devoluções podem alterar "
            "o valor recebido. Dados parciais enquanto pedidos não concluírem "
            "a sincronização."
        ),
    }
