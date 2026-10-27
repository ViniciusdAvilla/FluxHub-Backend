"""Authenticated, tenant-isolated durable sync job operations."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import (
    CompanyDataSourceModel, MercadoLivreSyncJobModel as Job, UserModel,
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
    elif job.status not in ("queued", "running", "retry"):
        # New full scan: keep existing snapshots; every write is an upsert.
        job.status = "queued"
        job.phase = "listings"
        job.scroll_id = None
        job.scroll_updated_at = None
        job.orders_offset = 0
        job.orders_window_start = None
        job.orders_window_end = None
        job.orders_until = None
        job.orders_span_seconds = 2592000
        job.listings_scanned = 0
        job.listings_total = None
        job.listings_saved = 0
        job.orders_saved = 0
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
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if account_id <= 0:
        raise HTTPException(422, "Conta inválida.")
    account = get_account(db, user.id, account_id)
    job = db.scalar(select(Job).where(
        Job.user_id == user.id,
        Job.mercado_livre_user_id == account.mercado_livre_user_id))
    if not job:
        return {"account_id": account_id, "status": "idle", "phase": None,
                "listings_scanned": 0, "listings_total": None, "listings_saved": 0, "orders_saved": 0,
                "pages_done": 0, "retry_count": 0, "last_error": None,
                "orders_window_start": None, "orders_window_end": None, "orders_until": None,
                "retry_at": None, "updated_at": None, "started_at": None,
                "completed_at": None}
    return _serialize(job)
