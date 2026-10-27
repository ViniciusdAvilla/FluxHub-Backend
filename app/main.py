import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.settings import settings
from sqlalchemy.exc import SQLAlchemyError
from app.database.order_enrichment_schema import (
    ensure_order_enrichment_schema, ensure_sync_job_schema,
)
from app.core.api_auth import protect_fastapi
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
from app.routers.connectors import router as connectors_router
from app.routers.mercado_livre import router as mercado_livre_router
from app.routers.mercado_pago import router as mercado_pago_router
from app.routers.support import router as support_router
from app.routers.analytics import router as analytics_router
from app.routers.sync_jobs import router as sync_jobs_router
from app.services.ml_sync_worker import run_sync_worker
from app.routers.product_performance import router as product_performance_router
from app.routers.atendimento import router as atendimento_router
from app.routers.health import router as health_router
from app.routers.products import router as products_router
from app.routers.orders import router as orders_router
from app.routers.indicators import router as indicators_router
from app.routers.stock import router as stock_router
from app.routers.sales import router as sales_router
from app.routers.dashboard import router as dashboard_router
from app.routers.pricing import router as pricing_router


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Old installations may not have run the additive enrichment migration.
    # Build only the missing feature tables before the sync worker uses them.
    schema_ready = True
    try:
        ensure_order_enrichment_schema()
    except SQLAlchemyError:
        schema_ready = False
        logger.exception("order_enrichment_schema_bootstrap_failed")
        # Keep the existing API and OAuth session online even when DDL is denied.
        # The manual idempotent migration remains available to an operator.
    try:
        ensure_sync_job_schema()
    except SQLAlchemyError:
        schema_ready = False
        logger.exception("sync_job_schema_bootstrap_failed")
    # A missing checkpoint column causes repeated UndefinedColumn errors.
    # Keep login/OAuth endpoints online, but do not run a broken worker.
    if settings.sync_worker_enabled and not schema_ready:
        logger.warning("ml_sync_worker_paused_missing_schema")
    task = asyncio.create_task(run_sync_worker()) if (
        settings.sync_worker_enabled and schema_ready
    ) else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version=settings.app_version
)


app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
app.include_router(connectors_router, prefix=settings.api_prefix)
app.include_router(mercado_livre_router, prefix=settings.api_prefix)
app.include_router(mercado_pago_router, prefix=settings.api_prefix)
app.include_router(support_router, prefix=settings.api_prefix)
app.include_router(analytics_router, prefix=settings.api_prefix)
app.include_router(sync_jobs_router, prefix=settings.api_prefix)
app.include_router(product_performance_router, prefix=settings.api_prefix)
app.include_router(atendimento_router, prefix=settings.api_prefix)
app.include_router(stock_router, prefix=settings.api_prefix)
app.include_router(sales_router, prefix=settings.api_prefix)

app.include_router(
    health_router,
    prefix=settings.api_prefix
)


app.include_router(
    products_router,
    prefix=settings.api_prefix
)


app.include_router(
    orders_router,
    prefix=settings.api_prefix
)


app.include_router(
    indicators_router,
    prefix=settings.api_prefix,
)


app.include_router(
    dashboard_router,
    prefix=settings.api_prefix,
)

app.include_router(pricing_router, prefix=settings.api_prefix)

# Protege todos os caminhos, inclusive /docs e /openapi.json.
protect_fastapi(app)
