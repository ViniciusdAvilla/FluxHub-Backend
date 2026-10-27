"""PostgreSQL-backed Mercado Livre sync runner (one small batch per lease).

The browser only starts a job. Progress is checkpointed independently of the
web request and recovered when the API process restarts. No credentials or item
payloads are stored in job rows.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.database.models import (
    CompanyDataSourceModel, MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderSnapshotModel as Order, MercadoLivreSyncJobModel as Job, UserModel,
)
from app.database.session import SessionLocal

logger = logging.getLogger(__name__)
LEASE_SECONDS = 600
# /orders/search is time-sliced so large stores do not rely on a deep offset.
ORDERS_WINDOW_SECONDS = 30 * 24 * 60 * 60
ORDERS_MAX_PAGE_WINDOW = 9000
MAX_RETRIES = 5
SCROLL_MAX_AGE = timedelta(minutes=4)


def utcnow():
    return datetime.now(timezone.utc)


def _claim_job() -> int | None:
    with SessionLocal() as db:
        now = utcnow()
        job = db.scalar(
            select(Job).where(
                Job.status.in_(("queued", "retry", "running")),
                or_(Job.lease_until.is_(None), Job.lease_until < now),
                or_(Job.retry_at.is_(None), Job.retry_at <= now),
            ).order_by(Job.updated_at, Job.id)
            .with_for_update(skip_locked=True).limit(1)
        )
        if job is None:
            return None

        # Mercado Livre scroll cursors expire quickly. If *all* API-reported
        # listings already exist in PostgreSQL and a page is repeatedly failing,
        # stop blocking order revenue behind another full catalog scan.
        # Previously stored listing rows are never deleted.
        if (job.phase == "listings" and job.retry_count > 0
                and job.listings_total is not None and job.listings_total > 0
                and job.listings_saved >= job.listings_total):
            persisted = db.scalar(select(func.count(Listing.id)).where(
                Listing.user_id == job.user_id,
                Listing.mercado_livre_user_id == job.mercado_livre_user_id)) or 0
            if persisted >= job.listings_total:
                logger.warning(
                    "ml_sync_orders_prioritized job_id=%s listings_present=%s expected=%s",
                    job.id, persisted, job.listings_total,
                )
                job.phase = "orders"
                job.scroll_id = None
                job.scroll_updated_at = None
                job.pending_listing_ids = None
                job.pending_next_scroll_id = None
                job.orders_offset = 0
                job.orders_window_start = None
                job.orders_window_end = None
                job.orders_until = None
                job.retry_count = 0
                job.last_error = "Anúncios preservados; atualização de pedidos priorizada."

        # A staged scan page is safe to replay after crashes, HTTP 502 or 429.
        # If no page was staged when the lease expired, start a fresh scan but
        # keep the true number of persisted listings; never lose DB progress.
        if job.status == "running" and job.phase == "listings" and job.pending_listing_ids is None:
            job.scroll_id = None
            job.scroll_updated_at = None
        if job.status == "running" and job.phase == "orders":
            job.orders_offset = 0
        if job.phase == "listings" and job.pending_listing_ids is None and job.scroll_id and (
            job.scroll_updated_at is None
            or now - job.scroll_updated_at > SCROLL_MAX_AGE
        ):
            job.scroll_id = None
            job.scroll_updated_at = None

        job.status = "running"
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        job.retry_at = None
        job.started_at = job.started_at or now
        job.updated_at = now
        db.commit()
        return job.id


async def process_one_sync_batch() -> bool:
    """Run a single bounded page, then release the database-backed lease."""
    job_id = _claim_job()
    if job_id is None:
        return False

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        user = db.get(UserModel, job.user_id) if job else None
        if job is None or user is None or not user.is_active:
            if job:
                job.status = "failed"
                job.last_error = "Usuário FluxHub indisponível."
                job.lease_until = None
                db.commit()
            return True
        try:
            if job.phase == "orders" and job.orders_window_start is None:
                # ML documents seller order history as available for up to
                # twelve months. A fixed horizon makes this run deterministic.
                horizon = utcnow()
                job.orders_until = horizon
                job.orders_window_start = horizon - timedelta(days=365)
                job.orders_span_seconds = ORDERS_WINDOW_SECONDS
                job.orders_window_end = min(
                    job.orders_window_start + timedelta(seconds=job.orders_span_seconds),
                    horizon)
                job.orders_offset = 0
                db.commit()
            from app.routers import analytics as analytics_router
            prefetched_page = None
            if job.phase == "listings":
                if job.pending_listing_ids is None:
                    from app.routers.mercado_livre import get_account
                    account = get_account(db, user.id, job.mercado_livre_user_id)
                    params = {"search_type": "scan"}
                    if job.scroll_id:
                        params["scroll_id"] = job.scroll_id
                    try:
                        page = await analytics_router.seller_request(
                            db, account, f"/users/{job.mercado_livre_user_id}/items/search", params)
                    except HTTPException as exc:
                        if exc.status_code == 429:
                            raise HTTPException(429, exc.detail, headers={
                                **(exc.headers or {}), "X-FluxHub-Safe-Retry": "true"
                            }) from exc
                        raise
                    if not isinstance(page, dict):
                        raise HTTPException(502, "Página de anúncios inválida.")
                    ids = page.get("results")
                    cursor = page.get("scroll_id")
                    if not isinstance(ids, list) or len(ids) > 100 or any(
                        not isinstance(item, str) or not item.isalnum() for item in ids
                    ):
                        raise HTTPException(502, "IDs de anúncios inválidos.")
                    if cursor is not None and (
                        not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096
                    ):
                        raise HTTPException(502, "Cursor de anúncios inválido.")
                    # Durable checkpoint BEFORE /items/bulk. Retry the exact
                    # same IDs when detail hydration fails.
                    job.pending_listing_ids = ids
                    job.pending_next_scroll_id = cursor if ids else None
                    job.scroll_updated_at = utcnow() if cursor else None
                    paging = page.get("paging")
                    if isinstance(paging, dict) and isinstance(paging.get("total"), int):
                        job.listings_total = paging["total"]
                    db.commit()
                prefetched_page = {
                    "results": job.pending_listing_ids,
                    "scroll_id": job.pending_next_scroll_id,
                    "paging": {"total": job.listings_total},
                }
            # Reuse the validated tenant-scoped upsert routine, bypassing a
            # second scan call when the worker has a staged page.
            result = await analytics_router._sync_batch(
                account_id=job.mercado_livre_user_id,
                offset=job.orders_offset if job.phase == "orders" else 0,
                limit=20,
                phase=job.phase,
                scroll_id=job.scroll_id if job.phase == "listings" else None,
                user=user,
                db=db,
                orders_created_from=job.orders_window_start if job.phase == "orders" else None,
                orders_created_to=job.orders_window_end if job.phase == "orders" else None,
                prefetched_page=prefetched_page,
            )
            if result.get("orders_unavailable"):
                raise HTTPException(502, "Consulta de pedidos indisponível.")
            # _sync_batch commits the data separately. Refresh the row to
            # respect any pause requested while the HTTP call was in flight.
            job = db.get(Job, job_id)
            db.refresh(job)
            was_paused = job.status == "paused"
            order_total = result.get("orders_page_total")
            next_order_offset = result.get("next_orders_offset")
            near_deep_offset = (
                isinstance(next_order_offset, int) and
                next_order_offset >= ORDERS_MAX_PAGE_WINDOW
            )
            if job.phase == "orders" and (
                (isinstance(order_total, int) and order_total >= ORDERS_MAX_PAGE_WINDOW)
                or near_deep_offset
            ):
                # Rewind this window and halve it before the 10k offset cliff.
                # Replayed upserts are safe; no orders are silently omitted.
                if job.orders_span_seconds <= 1:
                    raise HTTPException(422, "Intervalo de pedidos excede o limite da API.")
                job.orders_span_seconds = max(1, job.orders_span_seconds // 2)
                job.orders_window_end = min(
                    job.orders_window_start + timedelta(seconds=job.orders_span_seconds),
                    job.orders_until)
                job.orders_offset = 0
                job.status = "paused" if was_paused else "queued"
                job.lease_until = None
                job.updated_at = utcnow()
                db.commit()
                return True
            if job.phase == "listings" and isinstance(result.get("listings_total"), int):
                job.listings_total = result["listings_total"]
            # Progress is the unique persisted total, not the sum of repeated
            # attempts. Existing 11,517 items stay visible through retries.
            if job.phase == "listings":
                unique = db.scalar(select(func.count(Listing.id)).where(
                    Listing.user_id == job.user_id,
                    Listing.mercado_livre_user_id == job.mercado_livre_user_id)) or 0
                job.listings_scanned = unique
                job.listings_saved = unique
            else:
                job.orders_saved = db.scalar(select(func.count(Order.id)).where(
                    Order.user_id == job.user_id,
                    Order.mercado_livre_user_id == job.mercado_livre_user_id)) or 0
            job.pages_done += 1
            job.retry_count = 0
            job.status = "paused" if was_paused else "queued"
            job.last_error = None
            job.retry_at = None
            job.lease_until = None
            job.updated_at = utcnow()
            if job.phase == "listings":
                job.scroll_id = result.get("next_scroll_id")
                job.scroll_updated_at = (job.scroll_updated_at or utcnow()) if job.scroll_id else None
                job.pending_listing_ids = None
                job.pending_next_scroll_id = None
                if not job.scroll_id:
                    job.phase = "orders"
                    job.orders_offset = 0
            else:
                next_offset = result.get("next_orders_offset")
                if next_offset is None:
                    job.orders_offset = 0
                    job.orders_window_start = job.orders_window_end
                    if job.orders_window_start >= job.orders_until:
                        job.phase = "done"
                        job.status = "completed"
                        job.completed_at = utcnow()
                        source = db.scalar(select(CompanyDataSourceModel).where(
                            CompanyDataSourceModel.company_id == job.user_id,
                            CompanyDataSourceModel.provider == "mercado_livre",
                            CompanyDataSourceModel.account_key == f"ml:{job.mercado_livre_user_id}"))
                        if source:
                            source.last_synced_at = job.completed_at
                            source.status_message = (
                                f"Sincronização concluída: {job.listings_saved} anúncios e "
                                f"{job.orders_saved} pedidos processados em {job.pages_done} lotes.")
                    else:
                        job.orders_window_end = min(
                            job.orders_window_start + timedelta(seconds=job.orders_span_seconds),
                            job.orders_until)
                elif not isinstance(next_offset, int) or next_offset <= job.orders_offset:
                    raise ValueError("Progresso de pedidos inválido.")
                else:
                    job.orders_offset = next_offset
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if not job:
                return True
            db.refresh(job)
            if job.status == "paused":
                job.lease_until = None
                job.retry_at = None
                db.commit()
                return True
            status = exc.status_code if isinstance(exc, HTTPException) else 502
            job.retry_count += 1
            job.lease_until = None
            job.updated_at = utcnow()
            # Never persist upstream bodies, tokens, URLs or cursor values in logs.
            logger.warning(
                "ml_sync_batch_failed job_id=%s phase=%s http_status=%s attempts=%s error_type=%s staged_ids=%s",
                job_id, job.phase, status, job.retry_count,
                type(exc).__name__, len(job.pending_listing_ids or []),
            )
            if status in (401, 403, 404, 422):
                job.status = "failed"
                job.last_error = "Conexão ou dados inválidos; confira a conta Mercado Livre."
            elif status != 429 and job.retry_count >= MAX_RETRIES:
                job.status = "paused"
                job.last_error = ("Sincronização pausada após falhas repetidas; "
                                  "os anúncios já salvos foram preservados.")
            else:
                job.status = "retry"
                delay = min(300, 2 ** min(job.retry_count, 8)) + random.uniform(0, 1)
                if status == 429 and isinstance(exc, HTTPException):
                    retry_header = (exc.headers or {}).get("Retry-After", "")
                    if retry_header.isdecimal():
                        delay = max(delay, min(3600, int(retry_header)))
                job.retry_at = utcnow() + timedelta(seconds=delay)
                job.last_error = (
                    "Limite temporário de requisições (429); retomada automática."
                    if status == 429 else "Falha temporária na API; nova tentativa agendada."
                )
                # A staged page remains in PostgreSQL on failure. Do not
                # advance or discard its cursor, and never zero the counters.
            db.commit()
    return True


async def run_sync_worker() -> None:
    while True:
        try:
            worked = await process_one_sync_batch()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Database not migrated or temporarily unavailable. Keep the API up;
            # the job endpoint reports migration errors until deployed.
            logger.exception("ml_sync_worker_unavailable")
            worked = False
        await asyncio.sleep(0.25 if worked else 3)
