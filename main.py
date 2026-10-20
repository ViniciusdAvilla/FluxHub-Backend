<<<<<<< HEAD
from fastapi import FastAPI

from app.config.settings import settings
from app.routers.health import router as health_router
from app.routers.products import router as products_router
from app.routers.orders import router as orders_router
from app.routers.indicators import router as indicators_router
from app.routers.stock import router as stock_router
from app.routers.sales import router as sales_router
from app.routers.dashboard import router as dashboard_router
from app.routers.anuncios import router as anuncios_router
from app.routers.cancelamentos import router as cancelamentos_router

app.include_router(stock_router, prefix=settings.api_prefix)
app.include_router(sales_router, prefix=settings.api_prefix)

app.include_router(anuncios_router, prefix=settings.api_prefix)
app.include_router(cancelamentos_router, prefix=settings.api_prefix)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version
)


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
=======
"""Compatibilidade com deployments que usam `uvicorn main:app`.

A única instância da aplicação e seu middleware de segurança estão em app.main.
"""

from app.main import app

__all__ = ["app"]
>>>>>>> 0f0be375cc57f2cc8768c8753476903afcddb5c7
