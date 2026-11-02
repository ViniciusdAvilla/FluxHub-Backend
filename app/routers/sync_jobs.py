"""Authenticated, tenant-isolated durable sync job operations."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    CompanyDataSourceModel, MercadoLivreSyncJobModel as Job,
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderSnapshotModel as Order, UserModel,
)
from app.database.session import get_db
from app.routers.mercado_livre import get_account

router = APIRouter(prefix="/analytics/sync-jobs", tags=["Analytics Sync"])


def _serialize(job: Job):
    return {
        "account_id": job.mercado_livre_user_id,
        "status": job.status,
        "phase": job.phase,
        "listings_scanned": job.listings_scanned,
        "listings_total": job.listings_total,
        "listings_saved": job.listings_saved,
        "orders_saved": job.orders_saved,
        "pages_done": job.pages_done,
        "pending_listings_count": len(job.pending_listing_ids or []),
        "orders_window_start": job.orders_window_start,
        "orders_window_end": job.orders_window_end,
        "orders_until": job.orders_until,
        "retry_count": job.retry_count,
        "last_error": job.last_error,
        "retry_at": job.retry_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


@router.post("", status_code=202)
def start_sync_job(
    account_id: int = Query(ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account(db, user.id, account_id)
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.account_key == f"ml:{account.mercado_livre_user_id}",
    ).with_for_update())
    if source is None or not source.connected or not source.enabled:
        raise HTTPException(409, "Conecte e habilite a conta Mercado Livre.")

    job = db.scalar(select(Job).where(
        Job.user_id == user.id,
        Job.mercado_livre_user_id == account.mercado_livre_user_id))
    if job is None:
        job = Job(user_id=user.id, mercado_livre_user_id=account.mercado_livre_user_id)
        db.add(job)
    elif job.status == "paused":
        # Explicit resume: preserve staged IDs, OAuth e dados já salvos.
        job.status = "queued"
        job.retry_count = 0
        job.last_error = None
        job.retry_at = None
        job.lease_until = None
    elif job.status not in ("queued", "running", "retry"):
        # New full scan: keep existing snapshots; every write is an upsert.
        job.status = "queued"
        job.phase = "listings"
        job.scroll_id = None
        job.scroll_updated_at = None
        job.pending_listing_ids = None
        job.pending_next_scroll_id = None
        job.orders_offset = 0
        job.orders_window_start = None
        job.orders_window_end = None
        job.orders_until = None
        job.orders_span_seconds = 2592000
        # The snapshots survive full refreshes; counters must reflect reality.
        job.listings_saved = db.scalar(select(func.count(Listing.id)).where(
            Listing.user_id == user.id,
            Listing.mercado_livre_user_id == account.mercado_livre_user_id)) or 0
        job.listings_scanned = job.listings_saved
        job.listings_total = None
        job.orders_saved = db.scalar(select(func.count(Order.id)).where(
            Order.user_id == user.id,
            Order.mercado_livre_user_id == account.mercado_livre_user_id)) or 0
        job.pages_done = 0
        job.retry_count = 0
        job.last_error = None
        job.retry_at = None
        job.lease_until = None
        job.started_at = None
        job.completed_at = None
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _serialize(job)


@router.get("/{account_id}")
def sync_job_status(
    account_id: int,
    request: Request,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if account_id <= 0:
        raise HTTPException(422, "Conta inválida.")
    account = get_account(db, user.id, account_id)
    job = db.scalar(select(Job).where(
        Job.user_id == user.id,
        Job.mercado_livre_user_id == account.mercado_livre_user_id))
    worker_active = bool(getattr(request.app.state, "ml_sync_worker_active", False))
    if not job:
        return {"account_id": account_id, "worker_active": worker_active, "status": "idle", "phase": None,
                "listings_scanned": 0, "listings_total": None, "listings_saved": 0, "orders_saved": 0,
                "pages_done": 0, "retry_count": 0, "last_error": None,
                "orders_window_start": None, "orders_window_end": None, "orders_until": None,
                "retry_at": None, "updated_at": None, "started_at": None,
                "completed_at": None}
    return {**_serialize(job), "worker_active": worker_active}



@router.post("/{account_id}/pause")
def pause_sync_job(
    account_id: int,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stop claiming new pages; an already-running page may finish safely."""
    if account_id <= 0:
        raise HTTPException(422, "Conta inválida.")
    account = get_account(db, user.id, account_id)
    job = db.scalar(select(Job).where(
        Job.user_id == user.id,
        Job.mercado_livre_user_id == account.mercado_livre_user_id,
    ).with_for_update())
    if not job:
        raise HTTPException(404, "Sincronização não encontrada.")
    if job.status in ("queued", "running", "retry", "paused"):
        job.status = "paused"
        job.last_error = "Pausada manualmente pelo usuário."
        job.lease_until = None
        job.retry_at = None
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
    return _serialize(job)


@router.post("/{account_id}/orders", status_code=202)
def start_orders_only_job(
    account_id: int,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sync actual order snapshots without rescanning thousands of listings."""
    if account_id <= 0:
        raise HTTPException(422, "Conta inválida.")
    account = get_account(db, user.id, account_id)
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.account_key == f"ml:{account.mercado_livre_user_id}",
        CompanyDataSourceModel.enabled.is_(True),
        CompanyDataSourceModel.connected.is_(True),
    ))
    if source is None:
        raise HTTPException(409, "Conta Mercado Livre não está habilitada.")
    job = db.scalar(select(Job).where(
        Job.user_id == user.id,
        Job.mercado_livre_user_id == account.mercado_livre_user_id,
    ).with_for_update())
    if job is None:
        job = Job(user_id=user.id, mercado_livre_user_id=account.mercado_livre_user_id)
        db.add(job)
    elif job.status == "running":
        # O worker pode ter morrido sem liberar um lease antigo. Nesse caso
        # permitir sincronizar pedidos sem desconectar a conta do vendedor.
        stamp = datetime.now(timezone.utc)
        if job.lease_until is not None and job.lease_until > stamp:
            raise HTTPException(409, "O lote atual ainda está processando; aguarde sua conclusão.")
    job.status = "queued"
    job.phase = "orders"
    job.pending_listing_ids = None
    job.pending_next_scroll_id = None
    job.scroll_id = None
    job.scroll_updated_at = None
    job.orders_offset = 0
    job.orders_window_start = None
    job.orders_window_end = None
    job.orders_until = None
    job.orders_span_seconds = 2592000
    job.orders_saved = db.scalar(select(func.count(Order.id)).where(
        Order.user_id == user.id,
        Order.mercado_livre_user_id == account.mercado_livre_user_id)) or 0
    job.pages_done = 0
    job.retry_count = 0
    job.last_error = None
    job.retry_at = None
    job.lease_until = None
    job.started_at = None
    job.completed_at = None
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _serialize(job)
